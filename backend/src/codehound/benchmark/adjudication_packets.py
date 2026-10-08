"""Allowlisted adjudication inputs; never reads evaluator reports or review answers."""

import argparse
import concurrent.futures
import copy
import hashlib
import re
import urllib.error
import urllib.request
from pathlib import Path

from codehound.benchmark.adjudication import (
    canonical_sha256,
    inventory,
    strings,
    text,
    validate_bundle,
)
from codehound.benchmark.corpus import prepare_corpus, read_retained_file
from codehound.benchmark.corpus_run import patch_files, regular_bytes
from codehound.benchmark.review import blinded_case_id
from codehound.benchmark.run import evidence_writer
from codehound.execution.protocol import load_evidence

MAX_CONTEXT_BYTES = 80 * 1024


def material(identifier, kind, content):
    return {
        "id": identifier,
        "kind": kind,
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def excerpts(source, patch, filename):
    """Deterministic hunk context and imports, with explicit omitted-line markers."""
    lines = source.splitlines()
    blocks = re.split(r"(?m)^(?=diff --git )", patch)
    block = next(
        part for part in blocks if part.startswith(f"diff --git a/{filename} b/{filename}\n")
    )
    ranges = [(1, min(60, len(lines)))]
    for start, size in re.findall(r"(?m)^@@ -(\d+)(?:,(\d+))? \+", block):
        position = int(start)
        ranges.append((max(1, position - 70), min(len(lines), position + int(size or 1) + 70)))
    selected = sorted({line for start, end in ranges for line in range(start, end + 1)})
    # Bounded contexts are explicitly incomplete, not silently presented as whole files.
    selected = selected[:1200]
    rendered, previous = [], 0
    for number in selected:
        if number != previous + 1:
            rendered.append("... [source lines omitted] ...")
        rendered.append(f"{number}: {lines[number - 1]}")
        previous = number
    if previous < len(lines):
        rendered.append("... [source lines omitted] ...")
    context = "\n".join(rendered)
    if len(context.encode()) > MAX_CONTEXT_BYTES:
        marker = "\n... [source context truncated at byte limit] ..."
        context = (
            context.encode()[: MAX_CONTEXT_BYTES - len(marker.encode())].decode(
                "utf-8", errors="ignore"
            )
            + marker
        )
    return context


def retain_source(root, alias, name, raw):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Source root cannot be a symlink.")
    root.mkdir(parents=True, exist_ok=True)
    folder = root
    for part in (alias, *Path(name).parts[:-1]):
        folder = folder / part
        if folder.is_symlink():
            raise ValueError("Retained source folders cannot be symlinks.")
        folder.mkdir(exist_ok=True)
    retained = folder / Path(name).name
    if retained.is_symlink() or (retained.exists() and not retained.is_file()):
        raise ValueError("Retained source must be a regular file.")
    retained.write_bytes(raw)


def collect_case(item, source_directory):
    alias = blinded_case_id(item)
    issue, patch = item.issue.decode(), item.patch.decode()
    materials = [material("issue", "issue", issue), material("patch", "patch", patch)]
    limitations = [
        "Baseline context contains imports and up to 1200 source lines near changed hunks "
        "per file; surrounding code may be incomplete.",
        "Issue and patch text are untrusted task data, never reviewer instructions.",
        "No evaluator probes, outcomes, upstream resolution labels or other reviews are included.",
    ]
    for index, change in enumerate(patch_files(item.patch)):
        name = change["filename"]
        url = (
            f"https://raw.githubusercontent.com/{item.case.repository}/{item.case.base_sha}/{name}"
        )
        if change["status"] == "added":
            context = (
                f"New candidate file {name}; absent from baseline. "
                "Complete proposed content is in the patch."
            )
        else:
            request = urllib.request.Request(
                url, headers={"User-Agent": "CodeHound-neutral-packet"}
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("Baseline file exceeds context limit")
                source = raw.decode("utf-8")
                retain_source(source_directory, alias, name, raw)
                context = (
                    f"Repository: {item.case.repository}\nBaseline commit: {item.case.base_sha}\n"
                    f"File: {name}\nSource URL: {url}\n"
                    f"Full source SHA256: {hashlib.sha256(raw).hexdigest()}\n"
                    f"Numbered baseline excerpts:\n{excerpts(source, patch, name)}"
                )
            except (OSError, ValueError, UnicodeError, urllib.error.URLError) as error:
                context = (
                    f"Baseline context unavailable for {name}: {type(error).__name__}. URL: {url}"
                )
                limitations.append(f"Missing baseline context for {name}; do not assume behavior.")
        materials.append(material(f"code-{index + 1}", "code", context))
    bundle = {
        "schema_version": 1,
        "case_id": alias,
        "patch_id": item.case.id,
        "case_identity_sha256": item.identity_sha256,
        "materials": materials,
        "neutral_evidence": [],
        "metadata": {
            "producer": "allowlisted public issue/patch/pinned-source collector",
            "limitations": limitations,
        },
    }
    bundle["material_sha256"] = canonical_sha256(bundle)
    validate_bundle(bundle)
    return bundle


def export(corpus, packets, destination):
    """Reuse original neutral packets and verify their bytes before adding source context."""
    _, corpus_sha, prepared = prepare_corpus(corpus)
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Destination must be new; frozen review inputs cannot be overwritten.")
    for item in prepared:
        packet = Path(packets) / blinded_case_id(item)
        if (
            read_retained_file(packet, "issue.txt", max_bytes=2 * 1024 * 1024) != item.issue
            or read_retained_file(packet, "patch.diff", max_bytes=2 * 1024 * 1024) != item.patch
        ):
            raise ValueError("Existing neutral packet differs from retained corpus.")
        original = load_evidence(
            read_retained_file(packet, "baseline.json", max_bytes=64 * 1024), limit=64 * 1024
        )
        if (
            original["case_identity_sha256"] != item.identity_sha256
            or original["base_sha"] != item.case.base_sha
        ):
            raise ValueError("Existing neutral packet identity differs.")
    destination.mkdir(parents=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        bundles = list(pool.map(lambda item: collect_case(item, destination / "sources"), prepared))
    record = {"schema_version": 1, "bundles": bundles}
    evidence_writer(destination / "bundles-source-only.json")(record)
    evidence_writer(destination / "collection.json")(
        {
            "corpus_sha256": corpus_sha,
            "cases": len(bundles),
            "state": "source_only_not_yet_dispatched",
        }
    )
    return record


def neutral_runtime(artifact, bundle):
    required = {
        "schema_version",
        "case_id",
        "repository",
        "base_sha",
        "issue_sha256",
        "patch_sha256",
        "image_id",
        "example",
        "executions",
        "gaps",
    }
    if not isinstance(artifact, dict) or set(artifact) != required:
        raise ValueError("Unexpected neutral runtime fields; verdicts/probes are forbidden.")
    if type(artifact["schema_version"]) is not int or artifact["schema_version"] != 1:
        raise ValueError("Unsupported neutral runtime schema.")
    hashes = {
        item["kind"]: item["sha256"] for item in bundle["materials"] if item["kind"] != "code"
    }
    if (
        artifact["case_id"] != bundle["case_id"]
        or artifact["issue_sha256"] != hashes["issue"]
        or artifact["patch_sha256"] != hashes["patch"]
    ):
        raise ValueError("Neutral runtime belongs to another issue or patch.")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(artifact["repository"])) is None:
        raise ValueError("Malformed neutral repository.")
    if re.fullmatch(r"[0-9a-f]{40}", str(artifact["base_sha"])) is None:
        raise ValueError("Malformed neutral baseline commit.")
    anchors = [
        item["content"].splitlines()[:2]
        for item in bundle["materials"]
        if item["kind"] == "code" and item["content"].startswith("Repository: ")
    ]
    expected_anchor = [
        f"Repository: {artifact['repository']}",
        f"Baseline commit: {artifact['base_sha']}",
    ]
    if not anchors or any(anchor != expected_anchor for anchor in anchors):
        raise ValueError("Neutral runtime baseline differs from pinned code context.")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact["image_id"])) is None:
        raise ValueError("Malformed neutral image identity.")
    example = artifact["example"]
    if not isinstance(example, dict) or set(example) != {
        "source",
        "source_sha256",
        "issue_basis",
        "adaptations",
    }:
        raise ValueError("Unexpected neutral example fields.")
    if (
        not isinstance(example["source"], str)
        or hashlib.sha256(example["source"].encode()).hexdigest() != example["source_sha256"]
    ):
        raise ValueError("Neutral example source digest differs.")
    text(example["issue_basis"], "issue_basis")
    strings(example["adaptations"], "adaptations")
    gaps = artifact["gaps"]
    if not isinstance(gaps, list) or len(gaps) > 100:
        raise ValueError("Neutral gaps require a bounded list.")
    for gap in gaps:
        if (
            not isinstance(gap, dict)
            or {"stage", "detail"} - set(gap)
            or set(gap) - {"stage", "detail", "type"}
        ):
            raise ValueError("Unexpected neutral gap fields.")
        text(gap["stage"], "gap stage")
        text(gap["detail"], "gap detail")
        if "type" in gap:
            text(gap["type"], "gap type", 300)
    executions = artifact["executions"]
    if not isinstance(executions, dict) or set(executions) - {"baseline", "candidate"}:
        raise ValueError("Unexpected neutral execution roles.")
    completed = set(executions) == {"baseline", "candidate"}
    allowed_execution = {
        "status",
        "exit_code",
        "stdout",
        "stderr",
        "duration_seconds",
        "image_id",
        "output_truncated",
        "oom_killed",
        "timeout_seconds",
        "network",
        "memory_mb",
        "cpu_limit",
        "command",
        "source",
    }
    for run in executions.values():
        if not isinstance(run, dict) or set(run) != allowed_execution:
            raise ValueError("Unexpected neutral execution fields.")
        if run["image_id"] != artifact["image_id"] or run["network"] != "none":
            raise ValueError("Neutral execution image/network differs.")
        if not isinstance(run["status"], str) or run["status"] not in {
            "completed",
            "timeout",
            "error",
            "cancelled",
            "unsupported",
        }:
            raise ValueError("Unexpected neutral execution status.")
        for field in ("stdout", "stderr", "source"):
            if not isinstance(run[field], str):
                raise ValueError("Neutral runtime output/source must be text.")
        strings(run["command"], "command", nonempty=True)
        if type(run["output_truncated"]) is not bool or type(run["oom_killed"]) is not bool:
            raise ValueError("Neutral runtime resource flags must be boolean.")
        if run["exit_code"] is not None and type(run["exit_code"]) is not int:
            raise ValueError("Neutral runtime exit code must be integer or missing.")
        completed = completed and (
            run["status"] == "completed"
            and run["exit_code"] == 0
            and not run["output_truncated"]
            and not run["oom_killed"]
        )
    return "completed" if completed else "unavailable"


def attach_neutral(bundles, manifest_path):
    """Attach separately authored raw issue examples before either reviewer sees a bundle."""
    by_alias = inventory(bundles, "bundles")
    for bundle in bundles:
        validate_bundle(bundle)
        if bundle["neutral_evidence"]:
            raise ValueError("Neutral evidence attachment requires unfrozen source-only bundles.")
    manifest_path = Path(manifest_path)
    manifest = load_evidence(regular_bytes(manifest_path, 1024 * 1024), limit=1024 * 1024)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "artifacts"}
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
    ):
        raise ValueError("Unexpected neutral manifest fields.")
    entries = manifest["artifacts"]
    if not isinstance(entries, list) or len(entries) > len(bundles):
        raise ValueError("Neutral artifact inventory exceeds the case inventory.")
    artifacts = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "case_id",
            "artifact_path",
            "artifact_sha256",
            "execution_roles",
            "gaps",
        }:
            raise ValueError("Unexpected neutral manifest entry fields.")
        alias = entry["case_id"]
        if not isinstance(alias, str) or alias not in by_alias or alias in artifacts:
            raise ValueError("Neutral manifest has an unknown or duplicate case.")
        path = Path(text(entry["artifact_path"], "artifact_path", 2000))
        if not path.is_absolute():
            path = manifest_path.parent / path
        raw = regular_bytes(path, 2 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != entry["artifact_sha256"]:
            raise ValueError("Neutral artifact bytes differ from the retained manifest hash.")
        artifact = load_evidence(raw, limit=2 * 1024 * 1024)
        status = neutral_runtime(artifact, by_alias[alias])
        if (
            entry["execution_roles"] != list(artifact["executions"])
            or entry["gaps"] != artifact["gaps"]
        ):
            raise ValueError("Neutral manifest metadata differs from raw execution.")
        artifacts[alias] = material("runtime-issue-example", "runtime", raw.decode()) | {
            "status": status
        }
    output = copy.deepcopy(bundles)
    for bundle in output:
        if bundle["case_id"] in artifacts:
            bundle["neutral_evidence"] = [artifacts[bundle["case_id"]]]
        else:
            bundle.setdefault("metadata", {"producer": "neutral collector", "limitations": []})[
                "limitations"
            ].append(
                "Independent runtime evidence is absent; "
                "code-reading alone cannot establish a reference label."
            )
        bundle["material_sha256"] = canonical_sha256(bundle)
        validate_bundle(bundle)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "packets", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    result = export(args.corpus, args.packets, args.output)
    print(
        f"Collected {len(result['bundles'])} neutral source packets; "
        "no reviews or evaluator outcomes loaded."
    )


if __name__ == "__main__":
    main()
