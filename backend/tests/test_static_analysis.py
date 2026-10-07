import asyncio
import base64
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from codehound.execution.docker import ExecutionResult
from codehound.execution.inspection import static_ruff
from codehound.execution.static_analysis import (
    VERSION,
    analyze_static,
    compare_findings,
    inspect_static,
    validate_report,
)


def finding(path="subject.py", line=1, rule="F821", snippet="return unknown"):
    return {
        "path": path,
        "line": line,
        "column": 1,
        "end_line": line,
        "end_column": 2,
        "rule": rule,
        "message": "Undefined name `unknown`",
        "snippet": snippet,
        "source_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
    }


def report(findings=None, **changes):
    return {
        "schema_version": 1,
        "status": "completed",
        "tool_version": VERSION,
        "files": [{"path": "subject.py", "lines": 20, "bytes": 100}],
        "findings": findings or [],
        "skipped_files": [],
        "errors": [],
        "source_bytes": 100,
    } | changes


def test_line_shifts_existing_new_and_resolved():
    before = report([finding(), finding(rule="F401", snippet="import os")])
    after = report([finding(line=12), finding(rule="F841", snippet="unused = 1")])
    result = compare_findings({}, before, after)
    assert result["counts"] == {"new": 1, "resolved": 1, "existing": 1}
    assert result["existing"][0]["baseline_line"] == 1
    assert result["existing"][0]["line"] == 12
    assert result["new"][0]["rule"] == "F841"
    assert result["resolved"][0]["rule"] == "F401"


def test_duplicate_occurrences_and_renames_keep_counts():
    before = report([finding(), finding(line=2)])
    after = report([finding(path="renamed.py", line=8)] * 3)
    result = compare_findings(
        {
            "files": [
                {"status": "renamed", "previous_filename": "subject.py", "filename": "renamed.py"}
            ]
        },
        before,
        after,
    )
    assert result["counts"] == {"new": 1, "resolved": 0, "existing": 2}


@pytest.mark.parametrize(
    "mutation",
    [
        {"path": "../outside.py"},
        {"path": "/workspace/subject.py"},
        {"path": "missing.py"},
        {"line": True},
        {"line": 21},
        {"column": 0},
        {"end_line": 0},
        {"end_column": 0},
        {"rule": "S101"},
        {"source_sha256": "forged"},
        {"message": "x" * 2001},
        {"fix": {}},
    ],
)
def test_invalid_findings_rejected(mutation):
    with pytest.raises(ValueError):
        validate_report(report([finding() | mutation]))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "passed"},
        {"tool_version": "999"},
        {"schema_version": True},
        {"files": [{"path": "../subject.py", "lines": 20, "bytes": 100}]},
        {"files": [{"path": "subject.py", "lines": True, "bytes": 100}]},
        {"source_bytes": 99},
        {"errors": ["truncated"]},
        {"skipped_files": [{"path": "subject.py", "reason": "too_large"}]},
        {"extra": "untrusted"},
    ],
)
def test_invalid_or_incomplete_report_cannot_claim_completion(changes):
    with pytest.raises(ValueError):
        validate_report(report(**changes))


@pytest.mark.parametrize(
    "status,truncated,exit_code,raw",
    [
        ("timeout", False, 137, "[]"),
        ("output_limit", True, 0, "[]"),
        ("completed", True, 0, "[]"),
        ("completed", False, 2, "[]"),
        ("completed", False, 0, '{"schema_version":1,"schema_version":1}'),
        ("completed", False, 0, '{"bad":NaN}'),
        ("completed", False, 0, "[]"),
        ("completed", False, 0, "[" * 10000),
    ],
)
def test_container_and_malformed_json_fail_closed(
    monkeypatch, tmp_path, status, truncated, exit_code, raw
):
    async def fake_run(self, workspace, mounts, command):
        token = command[-1]
        assert command[:3] == ["python", "-I", "/harness/static_ruff.py"]
        assert mounts[0][1] == "/harness"
        frame = static_ruff.PREFIX + token + ":" + base64.b64encode(raw.encode()).decode()
        return ExecutionResult(
            status, exit_code, frame, "", 0.1, self.image_id, truncated, False, 30
        )

    monkeypatch.setattr(
        "codehound.execution.static_analysis.ContainerRunner.run_container", fake_run
    )
    result = asyncio.run(inspect_static(tmp_path, "sha256:" + "a" * 64))
    assert result["status"] == "inconclusive"
    assert result["errors"] and not result["findings"]


def test_valid_container_report_preserves_execution_evidence(monkeypatch, tmp_path):
    async def fake_run(self, workspace, mounts, command):
        frame = static_ruff.PREFIX + command[-1] + ":"
        frame += base64.b64encode(json.dumps(report([finding()])).encode()).decode()
        return ExecutionResult("completed", 0, frame, "", 0.1, self.image_id, False, False, 30)

    monkeypatch.setattr(
        "codehound.execution.static_analysis.ContainerRunner.run_container", fake_run
    )
    result = asyncio.run(inspect_static(tmp_path, "sha256:" + "a" * 64))
    assert result["status"] == "completed"
    assert result["findings"][0]["rule"] == "F821"
    assert result["execution"]["network"] == "none"


def test_incomplete_revision_cannot_label_findings_new_or_resolved(tmp_path):
    async def inspect(workspace, image):
        return (
            report([finding()])
            if workspace.name == "baseline"
            else report(status="inconclusive", errors=["timeout"])
        )

    checkouts = SimpleNamespace(baseline=tmp_path / "baseline", candidate=tmp_path / "candidate")
    result = asyncio.run(analyze_static({}, checkouts, "sha256:" + "a" * 64, inspect=inspect))
    assert result["status"] == "inconclusive"
    assert not result["new"] and not result["resolved"] and not result["existing"]
    assert result["baseline"]["findings"]
    assert result["coverage"]["comparison_complete"] is False


def test_harness_ignores_config_and_source_is_never_executed(tmp_path, monkeypatch):
    root, destination = tmp_path / "repo", tmp_path / "copied"
    root.mkdir()
    destination.mkdir()
    marker = tmp_path / "host-marker"
    (root / "subject.py").write_text(f"open({str(marker)!r}, 'w').write('executed')\nmissing\n")
    (root / "pyproject.toml").write_text('[tool.ruff.lint]\nignore = ["ALL"]\n')
    (root / ".gitignore").write_text("*.py\n")
    (root / "linked.py").symlink_to(root / "subject.py")
    files, skipped, _, _ = static_ruff.scan(root, destination)
    assert [item["path"] for item in files] == ["subject.py"]
    assert skipped == [{"path": "linked.py", "reason": "symlink_or_special_file"}]
    assert not marker.exists()
    assert not (destination / "pyproject.toml").exists()
    assert "--isolated" in static_ruff.FLAGS
    assert "--ignore-noqa" in static_ruff.FLAGS
    assert "--no-fix" in static_ruff.FLAGS


@pytest.mark.parametrize("reason", ["ruff_timeout", "ruff_output_limit", "ruff_version_mismatch"])
def test_harness_limits_remain_inconclusive_and_explain_reason(tmp_path, monkeypatch, reason):
    def fail_command(command):
        raise ValueError(reason)

    monkeypatch.setattr(static_ruff, "bounded_command", fail_command)
    result = static_ruff.analyze(tmp_path)
    assert result["status"] == "inconclusive"
    assert result["errors"] == [reason]
    assert not result["findings"]


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
def test_real_differential_ruff_ignores_candidate_suppression(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for directory in (baseline, candidate):
        directory.mkdir(mode=0o755)
    (baseline / "subject.py").write_text("import os\nvalue = 1\n")
    (candidate / "subject.py").write_text("\n\nimport os\nvalue = missing  # noqa\n")
    (candidate / "pyproject.toml").write_text('[tool.ruff.lint]\nignore = ["ALL"]\n')
    (candidate / ".gitignore").write_text("*.py\n")
    result = asyncio.run(
        analyze_static(
            {},
            SimpleNamespace(baseline=baseline, candidate=candidate),
            os.environ["CODEHOUND_TEST_IMAGE_ID"],
        )
    )
    assert result["status"] == "completed", result
    assert result["tool"]["version"] == VERSION
    assert result["counts"] == {"new": 1, "resolved": 0, "existing": 1}
    assert result["new"][0]["rule"] == "F821"
    assert result["existing"][0]["rule"] == "F401"


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
@pytest.mark.parametrize("source", ["def broken(:\n", "value = (\n", "value =\n", "if True:\n"])
def test_real_syntax_locations_including_virtual_eof(tmp_path, source):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for directory in (baseline, candidate):
        directory.mkdir(mode=0o755)
    (baseline / "subject.py").write_text("value = 1\n")
    (candidate / "subject.py").write_text(source)
    result = asyncio.run(
        analyze_static(
            {},
            SimpleNamespace(baseline=baseline, candidate=candidate),
            os.environ["CODEHOUND_TEST_IMAGE_ID"],
        )
    )
    assert result["status"] == "completed", result
    assert result["new"] and all(item["rule"] == "invalid-syntax" for item in result["new"])


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
def test_real_unicode_locations_symlink_coverage_and_no_code_execution(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for directory in (baseline, candidate):
        directory.mkdir(mode=0o755)
    (baseline / "subject.py").write_text("é = 1\n", encoding="utf-8")
    marker = tmp_path / "must-not-exist"
    (candidate / "subject.py").write_text(
        f"é = missing\nopen({str(marker)!r}, 'w').write('bad')\n", encoding="utf-8"
    )
    (candidate / ".ruff.toml").write_text("not valid TOML !!!")
    checkouts = SimpleNamespace(baseline=baseline, candidate=candidate)
    result = asyncio.run(analyze_static({}, checkouts, os.environ["CODEHOUND_TEST_IMAGE_ID"]))
    assert result["status"] == "completed", result
    finding = result["new"][0]
    assert finding["rule"] == "F821"
    assert (finding["column"], finding["end_column"]) == (5, 12)
    assert not marker.exists()
    (candidate / "outside.py").symlink_to(marker)
    incomplete = asyncio.run(analyze_static({}, checkouts, os.environ["CODEHOUND_TEST_IMAGE_ID"]))
    assert incomplete["status"] == "inconclusive"
    assert incomplete["candidate"]["skipped_files"][0]["reason"] == "symlink_or_special_file"
    assert not incomplete["new"]
