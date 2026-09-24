"""Compare test structure parsed by a trusted, non-executing container inspector."""

import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from uuid import uuid4

from codehound.execution.docker import ContainerRunner
from codehound.execution.protocol import load_evidence

PREFIX = "CODEHOUND_STRUCTURE_V1:"
STATUSES = {
    "parsed",
    "missing",
    "syntax_error",
    "symlink",
    "unsafe_path",
    "too_complex",
    "ambiguous_symbols",
    "too_large",
    "budget_exceeded",
    "unreadable",
}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)(/|$)|(^|/)test_[^/]+|[._](test|spec)\.", re.I)
LIMITATIONS = [
    "Only changed Python test paths are inspected; custom collection and aliases may be missed.",
    "Assertion and skip changes are review hints, not proof of weakened tests or intent.",
    "Source is parsed without executing candidate code. Dynamic behavior is not analyzed.",
]


def selected_files(files):
    result = []
    for file in files:
        before = file.get("previous_filename") or file["filename"]
        after = file["filename"]
        if any(path.endswith(".py") and TEST_PATH.search(path) for path in (before, after)):
            for path in (before, after):
                parts = PurePosixPath(path)
                if parts.is_absolute() or ".." in parts.parts or "\x00" in path:
                    raise ValueError("Unsafe test path")
            result.append(
                {"baseline_path": before, "candidate_path": after, "change": file["status"]}
            )
    return result


def validate_structure(value, paths):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "files"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Invalid structure report")
    files = value["files"]
    if not isinstance(files, list) or len(files) != len(paths):
        raise ValueError("Incomplete structure inventory")
    inventory = []
    for file in files:
        if (
            not isinstance(file, dict)
            or not isinstance(file.get("path"), str)
            or file.get("status") not in STATUSES
        ):
            raise ValueError("Invalid file result")
        inventory.append(file["path"])
        if file["status"] != "parsed":
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", file.get("sha256", "")):
            raise ValueError("Missing source identity")
        for group in ("functions", "assertions", "skips"):
            entries = file.get(group)
            if not isinstance(entries, list) or len(entries) > 2000:
                raise ValueError("Invalid symbol inventory")
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("scope"), str)
                    or len(entry["scope"]) > 4096
                    or type(entry.get("line")) is not int
                    or not 1 <= entry["line"] <= 1000000
                ):
                    raise ValueError("Invalid source location")
                if group == "functions":
                    if type(entry.get("is_test")) is not bool:
                        raise ValueError("Invalid function classification")
                elif not re.fullmatch(r"[0-9a-f]{64}", entry.get("signature", "")):
                    raise ValueError("Invalid expression identity")
    if sorted(inventory) != sorted(paths) or len(set(inventory)) != len(inventory):
        raise ValueError("Structure inventory differs")
    return {file["path"]: file for file in files}


async def inspect_revision(workspace, paths, image_id):
    if not paths:
        return {}, None
    payload = json.dumps({"paths": sorted(set(paths))}).encode() + b"\n"
    if len(payload) > 65536:
        return {}, "Test path inventory exceeds the inspection limit."
    token = uuid4().hex
    harness = Path(__file__).parent / "inspection"
    run = await ContainerRunner(image_id, timeout_seconds=30, output_limit=750000).run_container(
        workspace,
        [(harness, "/harness")],
        ["python", "-I", "/harness/test_structure.py", token],
        input_data=payload,
    )
    if run.status != "completed" or run.exit_code != 0:
        return {}, f"Inspector {run.status}; exit {run.exit_code}."
    marker = PREFIX + token + ":"
    frames = [line[len(marker) :] for line in run.stdout.splitlines() if line.startswith(marker)]
    if len(frames) != 1:
        return {}, "Inspector did not provide one complete report."
    try:
        report = load_evidence(base64.b64decode(frames[0], validate=True), limit=750000)
        return validate_structure(report, sorted(set(paths))), None
    except (ValueError, TypeError, KeyError, RecursionError):
        return {}, "Inspector returned invalid evidence."


def compare_structure(changes, baseline, candidate):
    findings, unverified = [], []
    for change in changes:
        before = baseline.get(change["baseline_path"], {"status": "missing"})
        after = candidate.get(change["candidate_path"], {"status": "missing"})
        locations = {
            "baseline_path": change["baseline_path"],
            "candidate_path": change["candidate_path"],
        }
        expected_missing_before = change["change"] == "added"
        expected_missing_after = change["change"] == "removed"
        if before["status"] != "parsed" and not (
            expected_missing_before and before["status"] == "missing"
        ):
            unverified.append(locations | {"revision": "baseline", "reason": before["status"]})
            continue
        if after["status"] != "parsed" and not (
            expected_missing_after and after["status"] == "missing"
        ):
            unverified.append(locations | {"revision": "candidate", "reason": after["status"]})
            continue
        old_functions = {item["scope"]: item for item in before.get("functions", [])}
        new_functions = {item["scope"]: item for item in after.get("functions", [])}
        for scope, item in old_functions.items():
            if item["is_test"] and scope not in new_functions:
                findings.append(
                    locations
                    | {
                        "kind": "test_removed",
                        "scope": scope,
                        "baseline_line": item["line"],
                        "candidate_line": None,
                        "message": "A previously present test function is absent or renamed.",
                    }
                )
        for group, kind, old, new in (
            ("assertions", "assertion_changed_or_removed", before, after),
            ("skips", "skip_added_or_changed", after, before),
        ):
            available = Counter(
                (entry["scope"], entry["signature"]) for entry in new.get(group, [])
            )
            for entry in old.get(group, []):
                identity = (entry["scope"], entry["signature"])
                if available[identity]:
                    available[identity] -= 1
                    continue
                adding = group == "skips"
                findings.append(
                    locations
                    | {
                        "kind": kind,
                        "scope": entry["scope"],
                        "baseline_line": None if adding else entry["line"],
                        "candidate_line": entry["line"]
                        if adding
                        else new_functions.get(entry["scope"], {}).get("line"),
                        "message": "A skip or expected-failure marker was added or changed."
                        if adding
                        else "An assertion changed or disappeared; review its strength.",
                    }
                )
    return findings, unverified


async def analyze_test_integrity(snapshot, checkouts, image_id, *, inspect=inspect_revision):
    changes = selected_files(snapshot.get("files", []))
    result = {
        "status": "not_applicable",
        "findings": [],
        "unverified": [],
        "files_examined": len(changes),
        "limitations": LIMITATIONS,
        "image_id": image_id,
    }
    if not changes:
        return result
    source = Path(__file__).parent / "inspection" / "test_structure.py"
    identity = hashlib.sha256(source.read_bytes() + Path(__file__).read_bytes()).hexdigest()
    result["inspector_sha256"] = identity
    before, before_error = await inspect(
        checkouts.baseline, [c["baseline_path"] for c in changes], image_id
    )
    after, after_error = await inspect(
        checkouts.candidate, [c["candidate_path"] for c in changes], image_id
    )
    if before_error or after_error:
        result.update(
            status="inconclusive",
            unverified=[
                {"revision": label, "reason": reason}
                for label, reason in (("baseline", before_error), ("candidate", after_error))
                if reason
            ],
        )
        return result
    if hashlib.sha256(source.read_bytes() + Path(__file__).read_bytes()).hexdigest() != identity:
        result.update(
            status="inconclusive",
            unverified=[{"revision": "both", "reason": "Inspector changed between revisions."}],
        )
        return result
    findings, unverified = compare_structure(changes, before, after)
    result.update(
        status="inconclusive" if unverified else "completed",
        findings=findings,
        unverified=unverified,
        baseline=before,
        candidate=after,
        inspector_sha256=identity,
    )
    return result
