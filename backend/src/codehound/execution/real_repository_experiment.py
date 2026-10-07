"""Synthetic failure demonstrations on immutable public packaging revisions."""

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from codehound.benchmark.run import evidence_writer, run_benchmark
from codehound.evaluation.registry import load_profiles
from codehound.evaluation.requirements import requirement_evidence
from codehound.execution.repository_tests import run_repository_tests
from codehound.execution.verify import suite_digest
from codehound.repositories.checkout import CheckoutFailure, GitWorkspace

BASE = "033854a05229074ddb191d67da1f8e0165e665da"
HEAD = "ca4e142149c04000c91c9f16642452e999ee04ba"
TARGET = "src/packaging/utils.py"
VARIANTS = (
    (
        "upstream-correct",
        "valid",
        "Upstream PR head with unknown authorship method; operator label covers only "
        "the configured name-validation contract, not general PR correctness.",
    ),
    (
        "overfit",
        "invalid",
        "Operator-authored synthetic mutation rejects only the visible "
        "newline example and leaves other valid names with trailing LF accepted.",
    ),
    (
        "regressive",
        "invalid",
        "Operator-authored synthetic mutation fixes validated names "
        "but incorrectly rejects newline names when validation is disabled.",
    ),
)
LIMITATIONS = [
    "Mutants and contract labels are operator-authored demonstrations, "
    "not sampled agent patches or human adjudications.",
    "The upstream control is historical upstream code with unknown authorship method; "
    "GitHub metadata does not establish whether AI was used.",
    "Public cases and two deliberate mutants do not estimate "
    "detection rates on real agent changes.",
    "Frozen repository pytest evidence is repository-controlled "
    "and never affects independent decisions.",
]


def copy_revision(source, destination, *, max_bytes=32 * 1024 * 1024, max_files=10000):
    """Copy bounded repository data; reject links/devices and omit Git metadata."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Revision source must be a regular directory.")
    destination.mkdir(mode=0o755)
    pending, count, total = [source], 0, 0
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            if path.name == ".git":
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Revision data may not contain symlinks.")
            target = destination / path.relative_to(source)
            if stat.S_ISDIR(mode):
                count += 1
                if count > max_files:
                    raise ValueError("Revision copy exceeds its entry limit.")
                target.mkdir(mode=0o755)
                pending.append(path)
            elif stat.S_ISREG(mode):
                count += 1
                if count > max_files or path.stat().st_size > max_bytes - total:
                    raise ValueError("Revision copy exceeds its size or entry limit.")
                with path.open("rb") as handle:
                    content = handle.read(max_bytes - total + 1)
                total += len(content)
                if total > max_bytes:
                    raise ValueError("Revision copy exceeds its size limit.")
                target.write_bytes(content)
                target.chmod(0o644)
            else:
                raise ValueError("Revision data must contain regular files/directories only.")
    return suite_digest(destination)


def apply_operator_patch(workspace, patch):
    """Apply one trusted, bounded source patch without running repository Python."""
    if patch.is_symlink() or not patch.is_file() or patch.stat().st_size > 32768:
        raise ValueError("Operator patch must be a regular file no larger than 32 KiB.")
    raw = patch.read_bytes()
    header = f"diff --git a/{TARGET} b/{TARGET}\n".encode()
    if (
        not raw.startswith(header)
        or raw.count(b"diff --git ") != 1
        or raw.count(b"\n--- ") != 1
        or raw.count(b"\n+++ ") != 1
        or f"--- a/{TARGET}\n+++ b/{TARGET}\n".encode() not in raw
    ):
        raise ValueError("Operator mutation must patch only the configured source file.")
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    command = [
        "git",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "credential.helper=",
        "apply",
        "--whitespace=error-all",
    ]
    for arguments in (("--check",), ()):
        result = subprocess.run(
            [*command, *arguments, "-"],
            input=raw,
            cwd=workspace,
            env=environment,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise ValueError("Operator mutation does not apply cleanly to the pinned baseline.")
    return hashlib.sha256(raw).hexdigest()


async def run_experiment(
    snapshot, mutations, image_id, *, checkpoint=None, progress=None, max_seconds=600
):
    if (
        snapshot.get("schema_version") != 1
        or snapshot.get("pull_request", {}).get("url")
        != "https://github.com/pypa/packaging/pull/925"
        or snapshot.get("base_sha") != BASE
        or snapshot.get("head_sha") != HEAD
        or snapshot.get("repository", {}).get("full_name") != "pypa/packaging"
        or snapshot.get("repository", {}).get("private") is not False
        or hashlib.sha256(snapshot["diff"].encode()).hexdigest() != snapshot["diff_sha256"]
    ):
        raise ValueError("Experiment requires the pinned public packaging PR #925 snapshot.")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 7200:
        raise ValueError("Experiment deadline must be between 1 and 7200 seconds.")
    profile = load_profiles()["packaging-name-validation"]
    artifact = {
        "schema_version": 1,
        "status": "running",
        "phase": "checkout",
        "experiment_kind": "synthetic_mutations_on_real_public_repository",
        "started_at": datetime.now(UTC).isoformat(),
        "image_id": image_id,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "upstream": {
            "repository": "pypa/packaging",
            "pr_url": snapshot["pull_request"]["url"],
            "base_sha": BASE,
            "head_sha": HEAD,
            "diff_sha256": snapshot["diff_sha256"],
            "authorship_method": "unknown",
        },
        "mutations": [],
        "benchmark": None,
        "limitations": list(LIMITATIONS),
    }

    def save():
        if checkpoint:
            checkpoint(artifact)

    save()
    try:
        async with asyncio.timeout(max_seconds):
            async with GitWorkspace("pypa/packaging", BASE, HEAD) as checkouts:
                with TemporaryDirectory(prefix="codehound-real-experiment-") as temporary:
                    root = Path(temporary)
                    root.chmod(0o755)
                    baseline = root / "baseline"
                    baseline_digest = copy_revision(checkouts.baseline, baseline)
                    for identifier, label, reason in VARIANTS:
                        directory = root / identifier
                        copy_revision(
                            checkouts.candidate
                            if identifier == "upstream-correct"
                            else checkouts.baseline,
                            directory,
                        )
                        patch_sha = None
                        if identifier != "upstream-correct":
                            patch_sha = apply_operator_patch(
                                directory, mutations / f"{identifier}.patch"
                            )
                        artifact["mutations"].append(
                            {
                                "candidate_id": identifier,
                                "label": label,
                                "label_reason": reason,
                                "origin": "upstream_patch"
                                if patch_sha is None
                                else "operator_synthetic_mutation",
                                "upstream_commit_sha": HEAD if patch_sha is None else None,
                                "mutation_base_sha": None if patch_sha is None else BASE,
                                "patch_sha256": patch_sha,
                                "workspace_sha256": suite_digest(directory),
                                "target_source_sha256": hashlib.sha256(
                                    (directory / TARGET).read_bytes()
                                ).hexdigest(),
                            }
                        )
                    for name, suite in (("visible", profile.visible), ("hidden", profile.hidden)):
                        (root / f"{name}.json").write_bytes(suite.canonical_bytes())
                    manifest = {
                        "schema_version": 1,
                        "name": "packaging PR #925 synthetic failure demonstration",
                        "dataset_kind": "synthetic_demo",
                        "provenance": "Public immutable pypa/packaging revisions "
                        "with one upstream control of unknown authorship method "
                        "and two operator mutations. "
                        "Contract labels are operator supplied; "
                        "no agent or human-review dataset.",
                        "tasks": [
                            {
                                "id": "packaging-name",
                                "family": "packaging-pr-925",
                                "split": "demo",
                                "issue": "Reject trailing LF names with validation enabled; "
                                "preserve normalization and validation-disabled behavior.",
                                "baseline": "baseline",
                                "visible_profile": "visible.json",
                                "independent_profile": "hidden.json",
                                "candidates": [
                                    {
                                        "id": identifier,
                                        "workspace": identifier,
                                        "label": label,
                                        "label_reason": reason,
                                    }
                                    for identifier, label, reason in VARIANTS
                                ],
                            }
                        ],
                    }
                    manifest_path = root / "manifest.json"
                    manifest_path.write_text(json.dumps(manifest))
                    artifact["phase"] = "independent"

                    def benchmark_checkpoint(record):
                        artifact["benchmark"] = record
                        save()

                    artifact["benchmark"] = await run_benchmark(
                        manifest_path,
                        image_id,
                        max_seconds=max_seconds,
                        progress=progress,
                        checkpoint=benchmark_checkpoint,
                    )
                    if artifact["benchmark"]["status"] != "completed":
                        artifact["status"] = artifact["benchmark"]["status"]
                    else:
                        artifact["phase"] = "repository_tests"
                        baseline_runs = artifact["benchmark"]["tasks"][0]["baseline"]
                        for row in artifact["benchmark"]["rows"]:
                            if progress:
                                progress(f"{row['candidate_id']}: frozen baseline repository tests")
                            comparison = SimpleNamespace(
                                baseline=baseline,
                                candidate=root / row["candidate_id"],
                                base_sha=BASE,
                            )
                            row["repository_tests"] = await run_repository_tests(
                                comparison, profile.repository_tests, image_id
                            )
                            row["requirement_evidence"] = requirement_evidence(
                                profile,
                                {
                                    "suites": {
                                        name: {
                                            "baseline": baseline_runs[name],
                                            "candidate": suite["candidate"],
                                        }
                                        for name, suite in row["suites"].items()
                                    }
                                },
                            )
                            if suite_digest(comparison.candidate) != row["workspace_sha256"]:
                                raise ValueError(
                                    "Candidate workspace changed during the experiment."
                                )
                            save()
                        if suite_digest(baseline) != baseline_digest:
                            raise ValueError("Baseline workspace changed during the experiment.")
                        for mutation in artifact["mutations"]:
                            if mutation["patch_sha256"] is not None:
                                raw = (mutations / f"{mutation['candidate_id']}.patch").read_bytes()
                                if hashlib.sha256(raw).hexdigest() != mutation["patch_sha256"]:
                                    raise ValueError(
                                        "Operator patch changed during the experiment."
                                    )
                        if (
                            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                            != artifact["evaluator_sha256"]
                        ):
                            raise ValueError("Experiment controller changed during execution.")
                        artifact["status"] = "completed"
    except TimeoutError:
        artifact["status"] = "timeout"
    except asyncio.CancelledError:
        artifact["status"] = "cancelled"
        save()
        raise
    except (ValueError, OSError, CheckoutFailure, subprocess.SubprocessError) as exc:
        artifact["status"] = "invalidated"
        artifact["error"] = str(exc)
        if artifact["benchmark"]:
            for row in artifact["benchmark"]["rows"]:
                row["assessment"] = None
                row["decisions"] = {"visible_only": "abstain", "independent": "abstain"}
            artifact["benchmark"]["metrics"] = None
    artifact["finished_at"] = datetime.now(UTC).isoformat()
    save()
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--mutations", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new evidence file.")
    if args.snapshot.stat().st_size > 32 * 1024 * 1024:
        parser.error("Snapshot exceeds 32 MiB.")
    artifact = asyncio.run(
        run_experiment(
            json.loads(args.snapshot.read_text()),
            args.mutations,
            args.image_id,
            checkpoint=evidence_writer(args.output),
            progress=lambda value: print(value, flush=True),
            max_seconds=args.max_seconds,
        )
    )
    print(f"Experiment {artifact['status']}; evidence: {args.output}")
    if artifact["benchmark"]:
        for row in artifact["benchmark"]["rows"]:
            print(
                f"{row['candidate_id']}: visible {row['decisions']['visible_only']}; "
                f"independent {row['decisions']['independent']}"
            )
    if artifact["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
