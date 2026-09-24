"""Capture a bounded, immutable PR evidence bundle without executing repository code."""

import hashlib
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from codehound.repositories.github_client import GitHubClient, GitHubFailure
from codehound.repositories.urls import PullReference

MAX_FILES = 300  # GitHub's pinned comparison API returns at most 300 changed files.
SHA = r"[0-9a-f]{40}"


class Repository(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(gt=0)
    full_name: str = Field(max_length=201)
    private: bool


class CommitRef(BaseModel):
    sha: str = Field(pattern=f"^{SHA}$")
    ref: str = Field(max_length=1024)
    repo: Repository | None


class Pull(BaseModel):
    model_config = ConfigDict(strict=True)
    number: int = Field(gt=0)
    title: str = Field(max_length=1024)
    body: str | None = Field(default=None, max_length=100000)
    state: str
    base: CommitRef
    head: CommitRef
    changed_files: int = Field(ge=0)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class ChangedFile(BaseModel):
    model_config = ConfigDict(strict=True)
    filename: str = Field(min_length=1, max_length=2048)
    previous_filename: str | None = Field(default=None, max_length=2048)
    status: str = Field(pattern="^(added|removed|modified|renamed|copied|changed|unchanged)$")
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    sha: str = Field(pattern=f"^{SHA}$")
    patch: str | None = Field(default=None, max_length=1_000_000)

    @field_validator("filename", "previous_filename")
    @classmethod
    def safe_relative_path(cls, value):
        if value and (value.startswith("/") or "\x00" in value or ".." in value.split("/")):
            raise ValueError("Unsafe repository-relative path")
        return value


def fingerprint(pull: Pull):
    return (
        pull.base.sha,
        pull.head.sha,
        pull.base.ref,
        pull.head.ref,
        pull.changed_files,
        pull.additions,
        pull.deletions,
        pull.state,
        pull.title,
        pull.body,
        pull.base.repo,
        pull.head.repo,
    )


def integrity_observations(files: list[ChangedFile]):
    """Surface review candidates, never treat filenames or string matches as proof."""
    findings = []
    for file in files:
        paths = [file.filename, file.previous_filename or ""]
        is_test = any(
            re.search(r"(^|/)(tests?|__tests__)(/|$)|(^|/)test_[^/]+|[._](test|spec)\.", p, re.I)
            for p in paths
        )
        if is_test:
            findings.append(
                {
                    "kind": "test_file_changed",
                    "severity": "review",
                    "path": file.filename,
                    "message": "A test file changed; review assertion strength and coverage.",
                }
            )
        if file.status == "removed":
            findings.append(
                {
                    "kind": "file_removed",
                    "severity": "review",
                    "path": file.filename,
                    "message": "A file was removed; inspect affected functionality and callers.",
                }
            )
        if any(
            p.rsplit("/", 1)[-1]
            in {
                "pytest.ini",
                "conftest.py",
                "tox.ini",
                "pyproject.toml",
                "package.json",
                "jest.config.js",
                "jest.config.ts",
                "vitest.config.ts",
                "setup.cfg",
            }
            for p in paths
        ):
            findings.append(
                {
                    "kind": "configuration_changed",
                    "severity": "review",
                    "path": file.filename,
                    "message": "Execution settings changed; compare with the trusted baseline.",
                }
            )
    return findings


async def collect_snapshot(reference: PullReference, github: GitHubClient):
    root = f"/repos/{reference.repository}"
    try:
        repo = Repository.model_validate(await github.json(root))
        if repo.private:
            raise GitHubFailure(
                "private_repository", "Only public repositories are supported.", 403
            )
        if repo.full_name.casefold() != reference.repository.casefold():
            raise GitHubFailure(
                "repository_moved", "Submit the repository's current canonical URL.", 409
            )
        pull_path = f"{root}/pulls/{reference.number}"
        pull = Pull.model_validate(await github.json(pull_path))
        if pull.number != reference.number or not pull.base.repo or pull.base.repo.id != repo.id:
            raise GitHubFailure(
                "snapshot_mismatch", "GitHub returned a PR for a different repository.", 409
            )
        if pull.state != "open":
            raise GitHubFailure(
                "unsupported_pr_state", "Intake currently supports open pull requests only.", 422
            )
        if not pull.head.repo or pull.head.repo.private or pull.base.repo.private:
            raise GitHubFailure(
                "unavailable_head", "The PR must have a public, available head repository.", 422
            )
        if pull.changed_files > MAX_FILES:
            raise GitHubFailure(
                "too_many_files", f"This PR exceeds the {MAX_FILES}-file intake limit.", 413
            )
        compare_path = f"{root}/compare/{pull.base.sha}...{pull.head.sha}"
        comparison = await github.json(compare_path, params={"per_page": 1, "page": 1})
        merge_base = comparison.get("merge_base_commit", {}).get("sha", "")
        if not re.fullmatch(SHA, merge_base):
            raise GitHubFailure(
                "missing_merge_base", "GitHub did not provide the comparison merge base."
            )
        if comparison.get("base_commit", {}).get("sha") != pull.base.sha:
            raise GitHubFailure(
                "snapshot_mismatch", "GitHub returned a different comparison base.", 409
            )
        raw_files = comparison.get("files")
        if not isinstance(raw_files, list) or len(raw_files) != pull.changed_files:
            raise GitHubFailure(
                "incomplete_file_list",
                "The file inventory is incomplete or changed during intake.",
                409,
            )
        files = [ChangedFile.model_validate(value) for value in raw_files]
        if len({file.filename for file in files}) != len(files):
            raise GitHubFailure(
                "invalid_github_response", "GitHub returned duplicate changed files."
            )
        if (
            sum(file.additions for file in files) != pull.additions
            or sum(file.deletions for file in files) != pull.deletions
        ):
            raise GitHubFailure(
                "snapshot_mismatch", "PR totals differ from the pinned comparison.", 409
            )
        diff = await github.diff(compare_path)
        if files and not diff.startswith("diff --git "):
            raise GitHubFailure("incomplete_diff", "GitHub did not return a usable patch.")
        if len(re.findall(r"^diff --git ", diff, re.MULTILINE)) != len(files):
            raise GitHubFailure(
                "incomplete_diff", "The diff inventory differs from the changed-file metadata."
            )
        # Fail rather than mixing metadata from different updates to a live PR.
        after = Pull.model_validate(await github.json(pull_path))
        if fingerprint(after) != fingerprint(pull):
            raise GitHubFailure(
                "pr_changed",
                "The PR changed during intake. Retry to capture its new revision.",
                409,
            )
        # Verify visibility again before retaining the bundle, including fork visibility.
        final_repo = Repository.model_validate(await github.json(root))
        if final_repo.private or final_repo.id != repo.id:
            raise GitHubFailure(
                "repository_changed",
                "Repository visibility or identity changed during intake.",
                409,
            )
    except (ValidationError, TypeError, AttributeError) as exc:
        raise GitHubFailure(
            "invalid_github_response", "GitHub returned incomplete or malformed metadata."
        ) from exc
    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "repository": repo.model_dump(),
        "pull_request": {
            "number": pull.number,
            "title": pull.title,
            "body": pull.body,
            "url": reference.url,
            "state": pull.state,
        },
        "base_target_sha": pull.base.sha,
        "base_sha": merge_base,
        "head_sha": pull.head.sha,
        "head_repository": pull.head.repo.full_name,
        "base_ref": pull.base.ref,
        "head_ref": pull.head.ref,
        "files": [
            file.model_dump(exclude={"patch"}) | {"patch_available": file.patch is not None}
            for file in files
        ],
        "diff": diff,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "summary": {
            "files_changed": len(files),
            "additions": pull.additions,
            "deletions": pull.deletions,
        },
        "observations": integrity_observations(files),
        "limitations": [
            "No repository code or tests have been executed.",
            "Filename-based integrity observations are review hints, not verdicts.",
            "GitHub diffs may omit binary contents; a checkout is required for execution.",
        ],
    }
