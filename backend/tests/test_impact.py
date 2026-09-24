import asyncio
import copy
import os
from types import SimpleNamespace

import pytest

from codehound.execution.impact import (
    analyze_python_impact,
    compare_repositories,
    downstream,
    import_graph,
    validate_inventory,
)
from codehound.execution.inspection.python_repository import scan


def tree(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return validate_inventory(scan(root))


def test_transitive_impact_follows_relative_and_absolute_imports(tmp_path):
    report = tree(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "src/app/tokens.py": "def valid(): return True",
            "src/app/session.py": "from .tokens import valid",
            "src/app/api.py": "from app import session",
            "src/app/unrelated.py": "import json",
        },
    )
    graph, unresolved, ambiguous = import_graph(report)
    assert "src/app/tokens.py" in graph["src/app/session.py"]
    assert "src/app/session.py" in graph["src/app/api.py"]
    affected = downstream(graph, {"src/app/tokens.py"})
    assert [(item["path"], item["distance"]) for item in affected] == [
        ("src/app/session.py", 1),
        ("src/app/api.py", 2),
    ]
    assert affected[-1]["import_chain"] == [
        "src/app/api.py",
        "src/app/session.py",
        "src/app/tokens.py",
    ]
    assert unresolved == 1 and ambiguous == 0


def test_ambiguous_modules_and_cycles_do_not_invent_impact(tmp_path):
    report = tree(
        tmp_path,
        {
            "one/src/pkg/a.py": "import pkg.b",
            "two/src/pkg/a.py": "",
            "src/pkg/b.py": "import pkg.a",
        },
    )
    graph, _, ambiguous = import_graph(report)
    assert ambiguous == 1
    assert not graph["src/pkg/b.py"]
    assert downstream({"a": {"b"}, "b": {"a"}}, {"a"})[0]["path"] == "b"


def test_only_new_syntax_errors_are_reported_and_removed_dependencies_remain_visible(tmp_path):
    baseline = tree(
        tmp_path / "old", {"a.py": "answer = 1", "caller.py": "import a", "existing.py": "def ("}
    )
    candidate = tree(
        tmp_path / "new", {"a.py": "def (", "caller.py": "import a", "existing.py": "def ("}
    )
    changes = {
        "files": [
            {"filename": "a.py", "status": "modified"},
            {"filename": "existing.py", "status": "modified"},
        ]
    }
    result = compare_repositories(changes, baseline, candidate)
    assert result["new_syntax_errors"] == [{"path": "a.py", "line": 1, "kind": "new_syntax_error"}]
    assert result["revisions"]["baseline"]["affected"][0]["path"] == "caller.py"
    assert result["status"] == "inconclusive"
    removed = compare_repositories(
        {"files": [{"filename": "a.py", "status": "removed"}]},
        baseline,
        tree(tmp_path / "removed", {"caller.py": "import a"}),
    )
    assert removed["revisions"]["baseline"]["affected_count"] == 1
    assert removed["revisions"]["candidate"]["affected_count"] == 0


def test_inspector_does_not_execute_source_or_follow_symlinks(tmp_path):
    marker = tmp_path / "MUST_NOT_EXIST"
    root = tmp_path / "repo"
    report = tree(root, {"danger.py": f"open({str(marker)!r}, 'w').write('bad')\nimport unknown"})
    assert report["files"][0]["status"] == "parsed" and not marker.exists()
    outside = tmp_path / "outside"
    tree(outside, {"secret.py": "value = 1"})
    (root / "linked.py").symlink_to(outside / "secret.py")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    report = scan(root)
    assert {item["path"]: item["status"] for item in report["files"]}["linked.py"] == "symlink"
    assert report["notices"][0]["reason"] == "symlink_directory"
    assert not any(item["path"].endswith("secret.py") for item in report["files"])


def test_inventory_validation_and_bounded_import_chains(tmp_path):
    report = tree(tmp_path, {"a.py": "import b"})
    for mutation in (
        lambda x: x["files"].append(x["files"][0]),
        lambda x: x["files"][0].update(path="../escape.py"),
        lambda x: x["files"][0]["imports"][0].update(level=-1),
    ):
        invalid = copy.deepcopy(report)
        mutation(invalid)
        with pytest.raises(ValueError):
            validate_inventory(invalid)
    graph = {f"{index}.py": {f"{index - 1}.py"} for index in range(1, 150)}
    last = downstream(graph, {"0.py"})[-1]
    assert last["distance"] == 149 and len(last["import_chain"]) == 32
    assert last["import_chain_truncated"] and last["import_chain"][-1] == "0.py"


def test_inspection_failure_does_not_claim_coverage(tmp_path):
    async def fail(*args):
        return None, "timeout"

    result = asyncio.run(
        analyze_python_impact(
            {"files": []},
            SimpleNamespace(baseline=tmp_path, candidate=tmp_path),
            "sha256:" + "a" * 64,
            inspect=fail,
        )
    )
    assert result["status"] == "inconclusive" and result["unverified_count"] == 2
    assert not result["revisions"]


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
def test_real_sandboxed_python_impact(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    tree(old, {"src/app/core.py": "value = 1", "src/app/api.py": "from .core import value"})
    tree(new, {"src/app/core.py": "value =", "src/app/api.py": "from .core import value"})
    for root in (old, new):
        root.chmod(0o755)
    result = asyncio.run(
        analyze_python_impact(
            {"files": [{"filename": "src/app/core.py", "status": "modified"}]},
            SimpleNamespace(baseline=old, candidate=new),
            os.environ["CODEHOUND_TEST_IMAGE_ID"],
        )
    )
    assert len(result["new_syntax_errors"]) == 1
    assert result["revisions"]["baseline"]["affected"][0]["path"] == "src/app/api.py"
    assert len(result["inspector_sha256"]) == 64


def test_scan_budget_exhaustion_is_visible(tmp_path, monkeypatch):
    from codehound.execution.inspection import python_repository

    tree(tmp_path, {"a.py": "value = 1", "b.py": "value = 2"})
    monkeypatch.setattr(python_repository, "MAX_FILES", 1)
    report = scan(tmp_path)
    assert len(report["files"]) == 1
    assert report["notices"] == [{"path": "", "reason": "file_limit"}]
    result = compare_repositories({"files": []}, report, report)
    assert result["status"] == "inconclusive"


def test_missing_scan_root_reports_unreadable_directory(tmp_path):
    report = scan(tmp_path / "missing")
    assert not report["files"]
    assert report["notices"] == [{"path": "", "reason": "unreadable_directory"}]
