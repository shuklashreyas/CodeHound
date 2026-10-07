"""Differential static findings from a pinned, isolated, offline Ruff evaluator."""

import base64
import binascii
import hashlib
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from uuid import uuid4

from codehound.execution.docker import ContainerRunner
from codehound.execution.inspection.static_ruff import FLAGS, LIMITS, PREFIX, VERSION, safe_path
from codehound.execution.protocol import load_evidence

LIMITATIONS = [
    "Only bounded regular Python .py/.pyi files are checked with Ruff E/F rules; .git is excluded.",
    "Repository configuration, noqa and gitignore controls are ignored; no fixes are applied.",
    "Findings are static review evidence, not proof of runtime regressions or task correctness.",
    "No type checking, dependency resolution, security audit or other-language analysis is done.",
    "Fixed Python 3.11 and line length 100 settings may differ from the project.",
    "Matching uses path, rule, message and normalized source line; line shifts are ignored.",
    "Edited source lines can appear resolved and new; identical occurrences are paired by count.",
    "Skipped files, timeout, output truncation or invalid evidence make comparison inconclusive.",
]
CONFIG = {"version": VERSION, "flags": FLAGS, "limits": LIMITS, "excluded": [".git"]}
CONFIG_SHA256 = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


def validate_report(report):
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "schema_version",
            "status",
            "tool_version",
            "files",
            "findings",
            "skipped_files",
            "errors",
            "source_bytes",
        }
        or type(report["schema_version"]) is not int
        or report["schema_version"] != 1
        or report["tool_version"] != VERSION
        or report["status"] not in {"completed", "inconclusive"}
    ):
        raise ValueError("Invalid static report")
    files = report["files"]
    if not isinstance(files, list) or len(files) > LIMITS["files"]:
        raise ValueError("Invalid static file inventory")
    paths = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "lines", "bytes"}:
            raise ValueError("Invalid static file")
        path = item["path"]
        if not safe_path(path) or not path.endswith((".py", ".pyi")) or path in paths:
            raise ValueError("Invalid static path")
        if (
            type(item["lines"]) is not int
            or not 1 <= item["lines"] <= LIMITS["file_bytes"] + 1
            or type(item["bytes"]) is not int
            or not 0 <= item["bytes"] <= LIMITS["file_bytes"]
        ):
            raise ValueError("Invalid static file bounds")
        paths[path] = item
    if (
        type(report["source_bytes"]) is not int
        or report["source_bytes"] != sum(file["bytes"] for file in files)
        or report["source_bytes"] > LIMITS["source_bytes"]
    ):
        raise ValueError("Invalid static source budget")
    findings = report["findings"]
    if not isinstance(findings, list) or len(findings) > LIMITS["findings"]:
        raise ValueError("Invalid finding count")
    for item in findings:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "rule",
            "message",
            "line",
            "column",
            "end_line",
            "end_column",
            "snippet",
            "source_sha256",
        }:
            raise ValueError("Invalid finding")
        if (
            not isinstance(item["path"], str)
            or item["path"] not in paths
            or not isinstance(item["rule"], str)
            or not re.fullmatch(r"(?:[EF][0-9]{3}|invalid-syntax)", item["rule"])
        ):
            raise ValueError("Invalid finding identity")
        for field, limit in (("message", 2000), ("snippet", 300)):
            if not isinstance(item[field], str) or len(item[field]) > limit:
                raise ValueError("Invalid finding text")
        if not isinstance(item["source_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", item["source_sha256"]
        ):
            raise ValueError("Invalid source digest")
        for field in ("line", "end_line", "column", "end_column"):
            bound = paths[item["path"]]["lines"] if "line" in field else LIMITS["file_bytes"] + 1
            if type(item[field]) is not int or not 1 <= item[field] <= bound:
                raise ValueError("Invalid finding location")
        if (item["end_line"], item["end_column"]) < (item["line"], item["column"]):
            raise ValueError("Invalid finding range")
    skips, errors = report["skipped_files"], report["errors"]
    if not isinstance(skips, list) or len(skips) > 200:
        raise ValueError("Invalid skip count")
    for item in skips:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "reason"}
            or not (item["path"] == "" or safe_path(item["path"]))
            or not isinstance(item["reason"], str)
            or not 0 < len(item["reason"]) <= 200
        ):
            raise ValueError("Invalid skip")
    if (
        not isinstance(errors, list)
        or len(errors) > 20
        or any(not isinstance(error, str) or len(error) > 2000 for error in errors)
    ):
        raise ValueError("Invalid errors")
    if report["status"] == "completed" and (errors or skips):
        raise ValueError("Incomplete static report cannot claim completion")
    return report


async def inspect_static(workspace, image_id):
    token = uuid4().hex
    run = await ContainerRunner(
        image_id, timeout_seconds=LIMITS["timeout_seconds"], output_limit=1000000
    ).run_container(
        workspace,
        [(Path(__file__).parent / "inspection", "/harness")],
        ["python", "-I", "/harness/static_ruff.py", token],
    )
    execution = {
        key: run.to_dict()[key]
        for key in (
            "status",
            "exit_code",
            "duration_seconds",
            "output_truncated",
            "oom_killed",
            "timeout_seconds",
            "network",
            "memory_mb",
            "cpu_limit",
        )
    }
    result = {
        "status": "inconclusive",
        "findings": [],
        "files": [],
        "skipped_files": [],
        "errors": [],
        "source_bytes": 0,
        "execution": execution,
    }
    if run.status != "completed" or run.exit_code != 0 or run.output_truncated or run.oom_killed:
        result["errors"].append(f"Static container {run.status}; exit {run.exit_code}.")
        return result
    prefix = PREFIX + token + ":"
    frames = [line[len(prefix) :] for line in run.stdout.splitlines() if line.startswith(prefix)]
    try:
        if len(frames) != 1 or run.stderr:
            raise ValueError("Missing or duplicate static report")
        report = validate_report(
            load_evidence(base64.b64decode(frames[0], validate=True), limit=LIMITS["report_bytes"])
        )
        return report | {"execution": execution}
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, binascii.Error):
        result["errors"].append("Static evaluator returned invalid evidence.")
        return result


def compare_findings(snapshot, baseline, candidate):
    renames = {
        item["previous_filename"]: item["filename"]
        for item in snapshot.get("files", [])
        if item.get("status") == "renamed"
        and safe_path(item.get("previous_filename"))
        and safe_path(item.get("filename"))
    }
    old = defaultdict(deque)

    def identity(item, *, before=False):
        path = renames.get(item["path"], item["path"]) if before else item["path"]
        return path, item["rule"], item["message"], item["source_sha256"]

    for item in baseline["findings"]:
        old[identity(item, before=True)].append(item)
    new, existing = [], []
    for item in candidate["findings"]:
        matches = old[identity(item)]
        if matches:
            previous = matches.popleft()
            existing.append(
                item | {"baseline_line": previous["line"], "baseline_path": previous["path"]}
            )
        else:
            new.append(item)
    resolved = [item for queue in old.values() for item in queue]
    return {
        "new": new,
        "resolved": resolved,
        "existing": existing,
        "counts": {"new": len(new), "resolved": len(resolved), "existing": len(existing)},
    }


async def analyze_static(snapshot, checkouts, image_id, *, inspect=inspect_static):
    sources = [Path(__file__), Path(__file__).parent / "inspection" / "static_ruff.py"]

    def evaluator_digest():
        return hashlib.sha256(b"".join(path.read_bytes() for path in sources)).hexdigest()

    fingerprint = evaluator_digest()
    baseline = await inspect(checkouts.baseline, image_id)
    candidate = await inspect(checkouts.candidate, image_id)
    complete = (
        baseline["status"] == candidate["status"] == "completed"
        and evaluator_digest() == fingerprint
    )
    result = {
        "status": "completed" if complete else "inconclusive",
        "image_id": image_id,
        "tool": {
            "name": "ruff",
            "version": VERSION,
            "rules": ["E", "F"],
            "config_sha256": CONFIG_SHA256,
        },
        "evaluator_sha256": fingerprint,
        "baseline": baseline,
        "candidate": candidate,
        "new": [],
        "resolved": [],
        "existing": [],
        "counts": {"new": 0, "resolved": 0, "existing": 0},
        "coverage": {
            "baseline_files": len(baseline["files"]),
            "candidate_files": len(candidate["files"]),
            "excluded_directories": [".git"],
            "comparison_complete": complete,
        },
        "limits": LIMITS,
        "limitations": LIMITATIONS,
    }
    if evaluator_digest() != fingerprint:
        result["limitations"] = [*LIMITATIONS, "Trusted evaluator changed during inspection."]
    return result | compare_findings(snapshot, baseline, candidate) if complete else result
