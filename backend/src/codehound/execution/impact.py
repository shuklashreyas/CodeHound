"""Conservative Python import impact and differential syntax evidence."""

import base64
import hashlib
import re
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from uuid import uuid4

from codehound.execution.docker import ContainerRunner
from codehound.execution.protocol import load_evidence

PREFIX = "CODEHOUND_PYTHON_REPOSITORY_V1:"
LIMITATIONS = [
    "Only Python files and static imports are inspected; candidate code is never imported.",
    "Import paths indicate possible impact, not proof of runtime reachability or a regression.",
    "Dynamic imports, plugins, external packages, and non-Python dependencies are not resolved.",
    "Source roots named src are inferred; ambiguous module names are left unresolved.",
    "Syntax uses the configured image interpreter, which may differ from the project runtime.",
    "Syntax checks are not linting, type checking, security analysis, or full regression testing.",
]


def safe_path(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and not PurePosixPath(value).is_absolute()
        and ".." not in PurePosixPath(value).parts
        and "\x00" not in value
    )


def validate_inventory(report):
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
    ):
        raise ValueError("Invalid inventory")
    files = report.get("files")
    if not isinstance(files, list) or len(files) > 2000:
        raise ValueError("Invalid file count")
    seen = set()
    for item in files:
        path = item.get("path")
        if not safe_path(path) or path in seen or not path.endswith(".py"):
            raise ValueError("Invalid file path")
        seen.add(path)
        if item.get("status") not in {
            "parsed",
            "syntax_error",
            "unreadable",
            "too_large",
            "too_complex",
            "budget_exceeded",
            "symlink",
        }:
            raise ValueError("Invalid inspection status")
        if item["status"] == "syntax_error" and (
            type(item.get("line")) is not int or not 1 <= item["line"] <= 1000000
        ):
            raise ValueError("Invalid syntax location")
        if item["status"] != "parsed":
            continue
        if (
            not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
            or not isinstance(item.get("imports"), list)
            or len(item["imports"]) > 1000
        ):
            raise ValueError("Invalid parsed source")
        for entry in item["imports"]:
            if not isinstance(entry.get("module"), str) or len(entry["module"]) > 4096:
                raise ValueError("Invalid import")
            if (
                not isinstance(entry.get("names"), list)
                or len(entry["names"]) > 1000
                or any(not isinstance(name, str) or len(name) > 4096 for name in entry["names"])
            ):
                raise ValueError("Invalid imported names")
            if (
                type(entry.get("level")) is not int
                or not 0 <= entry["level"] <= 1000
                or type(entry.get("line")) is not int
                or not 1 <= entry["line"] <= 1000000
            ):
                raise ValueError("Invalid import location")
    notices = report.get("notices")
    if not isinstance(notices, list) or len(notices) > 200:
        raise ValueError("Invalid coverage notices")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("reason"), str)
        or len(item["reason"]) > 100
        or not (item.get("path") == "" or safe_path(item.get("path")))
        for item in notices
    ):
        raise ValueError("Invalid coverage notice")
    if type(report.get("notices_truncated")) is not bool:
        raise ValueError("Invalid notice truncation")
    if not isinstance(report.get("excluded_directories"), list) or any(
        not isinstance(name, str) or len(name) > 100 for name in report["excluded_directories"]
    ):
        raise ValueError("Invalid exclusions")
    return report


async def inspect_repository(workspace, image_id):
    token = uuid4().hex
    run = await ContainerRunner(image_id, timeout_seconds=30, output_limit=1000000).run_container(
        workspace,
        [(Path(__file__).parent / "inspection", "/harness")],
        ["python", "-I", "/harness/python_repository.py", token],
    )
    if run.status != "completed" or run.exit_code != 0 or run.output_truncated:
        return None, f"Repository inspector {run.status}; exit {run.exit_code}."
    prefix = PREFIX + token + ":"
    frames = [line[len(prefix) :] for line in run.stdout.splitlines() if line.startswith(prefix)]
    if len(frames) != 1:
        return None, "Repository inspector did not return one complete report."
    try:
        return validate_inventory(
            load_evidence(base64.b64decode(frames[0], validate=True), limit=750000)
        ), None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None, "Repository inspector returned invalid evidence."


def module_aliases(path):
    parts = PurePosixPath(path).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    aliases = {".".join(parts)}
    for index, part in enumerate(parts):
        if part == "src" and index + 1 < len(parts):
            aliases.add(".".join(parts[index + 1 :]))
    return {name for name in aliases if name}


def import_graph(report):
    candidates = defaultdict(set)
    for file in report["files"]:
        for alias in module_aliases(file["path"]):
            candidates[alias].add(file["path"])
    resolved = {name: next(iter(paths)) for name, paths in candidates.items() if len(paths) == 1}
    graph = defaultdict(set)
    unresolved = 0
    for file in report["files"]:
        for entry in file.get("imports", []):
            targets = set()
            if entry["level"]:
                for alias in module_aliases(file["path"]):
                    package = (
                        alias.split(".")
                        if file["path"].endswith("/__init__.py")
                        else alias.split(".")[:-1]
                    )
                    if entry["level"] > len(package):
                        continue
                    base = ".".join(package[: len(package) - entry["level"] + 1])
                    targets.add(".".join(part for part in (base, entry["module"]) if part))
            else:
                targets.add(entry["module"])
            expanded = targets | {
                f"{target}.{name}" for target in targets for name in entry["names"] if name != "*"
            }
            dependencies = {resolved[name] for name in expanded if name in resolved} - {
                file["path"]
            }
            if not dependencies:
                unresolved += 1
            graph[file["path"]].update(dependencies)
    return graph, unresolved, sum(len(paths) > 1 for paths in candidates.values())


def downstream(graph, changed):
    reverse = defaultdict(set)
    for consumer, dependencies in graph.items():
        for dependency in dependencies:
            reverse[dependency].add(consumer)
    queue = deque((path, 0) for path in sorted(changed))
    parent = {path: None for path in changed}
    origins = {path: path for path in changed}
    affected = []
    while queue:
        path, distance = queue.popleft()
        for consumer in sorted(reverse[path]):
            if consumer in parent:
                continue
            parent[consumer] = path
            origins[consumer] = origins[path]
            trail, cursor = [], consumer
            while cursor is not None and len(trail) < 31:
                trail.append(cursor)
                cursor = parent[cursor]
            truncated = cursor is not None
            if truncated:
                trail.append(origins[consumer])
            affected.append(
                {
                    "path": consumer,
                    "distance": distance + 1,
                    "import_chain": trail,
                    "import_chain_truncated": truncated,
                }
            )
            queue.append((consumer, distance + 1))
    return affected


def compare_repositories(snapshot, baseline, candidate):
    changes = snapshot.get("files", [])
    changed = {
        path
        for file in changes
        for path in (file["filename"], file.get("previous_filename"))
        if path
    }
    old = {item["path"]: item for item in baseline["files"]}
    new = {item["path"]: item for item in candidate["files"]}
    syntax = []
    uncertain = []
    for change in changes:
        before = old.get(change.get("previous_filename") or change["filename"])
        after = new.get(change["filename"])
        if after and after["status"] == "syntax_error":
            if (before and before["status"] == "parsed") or change["status"] == "added":
                syntax.append(
                    {"path": after["path"], "line": after["line"], "kind": "new_syntax_error"}
                )
    graphs = {}
    for revision, report in (("baseline", baseline), ("candidate", candidate)):
        graph, unresolved, ambiguous = import_graph(report)
        affected = downstream(graph, changed)
        graphs[revision] = {
            "python_files": len(report["files"]),
            "parsed_files": sum(item["status"] == "parsed" for item in report["files"]),
            "resolved_edges": sum(len(edges) for edges in graph.values()),
            "unresolved_imports": unresolved,
            "ambiguous_modules": ambiguous,
            "affected_count": len(affected),
            "affected": affected[:100],
            "affected_truncated": len(affected) > 100,
            "excluded_directories": report["excluded_directories"],
        }
        uncertain.extend(
            {"revision": revision, "path": item["path"], "reason": item["status"]}
            for item in report["files"]
            if item["status"] != "parsed"
        )
        uncertain.extend({"revision": revision, **item} for item in report["notices"])
    return {
        "status": "inconclusive" if uncertain else "completed",
        "revisions": graphs,
        "new_syntax_errors": syntax,
        "unverified": uncertain[:200],
        "unverified_count": len(uncertain),
    }


async def analyze_python_impact(snapshot, checkouts, image_id, *, inspect=inspect_repository):
    source = Path(__file__).parent / "inspection" / "python_repository.py"

    def identity():
        return hashlib.sha256(source.read_bytes() + Path(__file__).read_bytes()).hexdigest()

    fingerprint = identity()
    result = {
        "status": "inconclusive",
        "image_id": image_id,
        "inspector_sha256": fingerprint,
        "limitations": LIMITATIONS,
        "revisions": {},
        "new_syntax_errors": [],
        "unverified": [],
        "unverified_count": 0,
    }
    before, before_error = await inspect(checkouts.baseline, image_id)
    after, after_error = await inspect(checkouts.candidate, image_id)
    if before_error or after_error or identity() != fingerprint:
        result["unverified"] = [
            {"revision": revision, "path": "", "reason": reason}
            for revision, reason in (("baseline", before_error), ("candidate", after_error))
            if reason
        ]
        if identity() != fingerprint:
            result["unverified"].append(
                {"revision": "both", "path": "", "reason": "Inspector changed between revisions."}
            )
        result["unverified_count"] = len(result["unverified"])
        return result
    return result | compare_repositories(snapshot, before, after)
