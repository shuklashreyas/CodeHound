import asyncio
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from codehound.evaluation.job_schemas import execution_checks
from codehound.evaluation.schemas import unrun_checks
from codehound.execution.inspection.test_structure import inspect_files, inspect_source
from codehound.execution.integrity import (
    analyze_test_integrity,
    compare_structure,
    inspect_revision,
    selected_files,
    validate_structure,
)


def parsed(source, path="tests/test_example.py"):
    return {"path": path, "sha256": hashlib.sha256(source.encode()).hexdigest()} | inspect_source(
        source
    )


def compare(
    before,
    after,
    *,
    status="modified",
    before_path="tests/test_example.py",
    after_path="tests/test_example.py",
):
    changes = [{"baseline_path": before_path, "candidate_path": after_path, "change": status}]
    old = {before_path: parsed(before, before_path)} if before is not None else {}
    new = {after_path: parsed(after, after_path)} if after is not None else {}
    return compare_structure(changes, old, new)


def test_assertion_predicate_changes_are_review_findings():
    findings, unverified = compare(
        "def test_answer():\n assert result == expected\n",
        "def test_answer():\n assert result is not None\n",
    )
    assert not unverified
    assert [item["kind"] for item in findings] == ["assertion_changed_or_removed"]
    assert findings[0]["baseline_line"] == 2 and findings[0]["candidate_line"] == 1


def test_cosmetic_assertion_message_and_line_changes_are_ignored():
    findings, unverified = compare(
        "def test_answer():\n assert x == 1, 'old'\n",
        "\n\ndef test_answer():\n assert x == 1, 'better message'\n",
    )
    assert findings == unverified == []


def test_removed_test_file_and_functions_keep_baseline_locations():
    findings, unverified = compare("def test_answer():\n assert x == 1\n", None, status="removed")
    assert not unverified
    assert {item["kind"] for item in findings} == {"test_removed", "assertion_changed_or_removed"}
    assert all(item["candidate_line"] is None for item in findings)


def test_renamed_test_file_is_compared_by_previous_filename():
    source = "def test_answer():\n assert x == 1\n"
    assert compare(source, source, status="renamed", after_path="tests/test_renamed.py") == ([], [])
    assert (
        selected_files(
            [
                {
                    "filename": "src/renamed.py",
                    "previous_filename": "tests/test_example.py",
                    "status": "renamed",
                }
            ]
        )[0]["baseline_path"]
        == "tests/test_example.py"
    )


def test_adding_tests_is_not_a_weakening_finding():
    assert compare(None, "def test_new():\n assert x == 1\n", status="added") == ([], [])


def test_unittest_and_pytest_raises_calls_are_compared():
    before = (
        "class TestAnswer:\n def test_answer(self):\n  self.assertEqual(x, 2)\n"
        "  with pytest.raises(ValueError):\n   fn()\n"
    )
    after = before.replace("self.assertEqual(x, 2)", "self.assertTrue(x)").replace(
        "ValueError", "Exception"
    )
    findings, _ = compare(before, after)
    assert len(findings) == 2
    assert {item["scope"] for item in findings} == {"TestAnswer.test_answer"}


def test_duplicate_assertions_are_counted_without_double_counting_skip_calls():
    before = "def test_answer():\n assert x\n assert x\n"
    after = "@pytest.mark.skip(reason='skip')\ndef test_answer():\n assert x\n"
    findings, _ = compare(before, after)
    assert [item["kind"] for item in findings].count("assertion_changed_or_removed") == 1
    assert [item["kind"] for item in findings].count("skip_added_or_changed") == 1


def test_ambiguous_symbols_and_missing_files_are_unverified():
    source = "def test_a(): pass\ndef test_a(): pass\n"
    assert inspect_source(source)["status"] == "ambiguous_symbols"
    assert compare("def test_a(): pass\n", None)[1][0]["reason"] == "missing"


def test_source_inspection_does_not_execute_code_and_refuses_symlinks(tmp_path):
    (tmp_path / "test_safe.py").write_text(
        "raise RuntimeError('never executed')\ndef test_a(): assert True\n"
    )
    (tmp_path / "test_link.py").symlink_to(tmp_path / "test_safe.py")
    (tmp_path / "test_invalid.py").write_text("def !!!")
    results = inspect_files(
        tmp_path, ["test_safe.py", "test_link.py", "test_invalid.py", "../outside"]
    )
    assert [item["status"] for item in results] == [
        "parsed",
        "symlink",
        "syntax_error",
        "unsafe_path",
    ]


def test_partial_inspection_does_not_hide_findings():
    path = "test_bad.py"
    before = {path: parsed("def test_a(): assert True", path)}
    after = {path: parsed("def test_a(): pass", path)}
    changes = [
        {"baseline_path": path, "candidate_path": path, "change": "modified"},
        {"baseline_path": "test_other.py", "candidate_path": "test_other.py", "change": "modified"},
    ]
    findings, unverified = compare_structure(changes, before, after)
    assert len(findings) == len(unverified) == 1


def test_structure_report_requires_complete_unique_inventory():
    file = parsed("def test_a(): assert True")
    assert validate_structure({"schema_version": 1, "files": [file]}, [file["path"]])
    with pytest.raises(ValueError):
        validate_structure(
            {"schema_version": 1, "files": [file, file]}, [file["path"], file["path"]]
        )
    with pytest.raises(ValueError):
        validate_structure({"schema_version": 1, "files": []}, [file["path"]])


def test_api_integrity_status_remains_review_not_malicious_or_pass():
    checks = unrun_checks()
    job = SimpleNamespace(
        status="completed",
        artifact={
            "suites": {},
            "test_integrity": {
                "status": "completed",
                "files_examined": 1,
                "findings": [{}],
                "unverified": [],
            },
        },
    )
    execution_checks(checks, job)
    result = next(check for check in checks if check.name == "test_integrity")
    assert result.status == "needs_review" and "not proof" in result.explanation
    assert next(check for check in checks if check.name == "task_completion").status == "not_run"


def test_pipeline_inspector_failures_are_recorded_without_a_false_clean_result():
    async def failure(*_):
        return {}, "Inspector timeout."

    result = asyncio.run(
        analyze_test_integrity(
            {"files": [{"filename": "test_a.py", "status": "modified"}]},
            SimpleNamespace(baseline=Path("a"), candidate=Path("b")),
            "sha256:" + "a" * 64,
            inspect=failure,
        )
    )
    assert result["status"] == "inconclusive" and len(result["unverified"]) == 2


def test_docker_inspection_never_imports_candidate_tests(tmp_path):
    image = os.getenv("CODEHOUND_TEST_IMAGE_ID")
    if not image:
        pytest.skip("Trusted Docker image not configured")
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o755)
    (workspace / "test_candidate.py").write_text(
        "import os\nos._exit(99)\ndef test_answer(): assert 1 == 1\n"
    )
    results, error = asyncio.run(inspect_revision(workspace, ["test_candidate.py"], image))
    assert error is None
    assert results["test_candidate.py"]["status"] == "parsed"
    assert len(results["test_candidate.py"]["assertions"]) == 1
