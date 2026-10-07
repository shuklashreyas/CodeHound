import base64
import json

import pytest

from codehound.benchmark.task_experiment import (
    TaskProfile,
    compare_observations,
    load_profile,
    observation,
    same_json,
)
from codehound.execution.docker import ExecutionResult


def observed(values):
    return {"usable": True, "values": values}


def test_separates_improvements_regressions_and_unresolved():
    expected = {"fixed": 2, "broken": 2, "still_wrong": 2, "control": 2}
    result = compare_observations(
        observed({"fixed": 1, "broken": 2, "still_wrong": 1, "control": 2}),
        observed({"fixed": 2, "broken": 1, "still_wrong": 1, "control": 2}),
        expected,
    )
    assert result["decision"] == "reject"
    assert result["improvements"] == ["fixed"]
    assert result["regressions"] == ["broken"]
    assert result["unresolved"] == ["still_wrong"]


def test_missing_baseline_is_abstention_not_rejection():
    assert (
        compare_observations({"usable": False}, observed({"a": 1}), {"a": 1})["decision"]
        == "abstain"
    )


def test_booleans_do_not_equal_numbers_in_nested_observations():
    assert not same_json({"a": [True]}, {"a": [1]})
    assert same_json({"a": [1.0]}, {"a": [1]})
    assert not same_json([1], {"0": 1})


def test_protocol_requires_complete_scenario_inventory_and_completed_execution():
    token = "a" * 32
    encoded = base64.b64encode(
        json.dumps({"kind": "returned", "value": {"a": 1}}).encode()
    ).decode()
    run = ExecutionResult(
        "completed",
        0,
        f"CODEHOUND_CALL_V1:{token}:{encoded}",
        "",
        0.1,
        "sha256:" + "1" * 64,
        False,
        False,
        30,
    )
    assert observation(run, token, {"a": 1})["usable"]
    assert not observation(run, token, {"a": 1, "b": 2})["usable"]
    assert not observation(run, "b" * 32, {"a": 1})["usable"]


def test_supported_profile_cannot_hide_missing_probes():
    with pytest.raises(ValueError):
        TaskProfile.model_validate(
            {
                "task_id": "one",
                "repository": "a/b",
                "base_commit": "a" * 40,
                "issue_sha256": "b" * 64,
                "supported": True,
                "unsupported_reason": None,
                "expected": {},
                "authorship": {
                    "scope": "development",
                    "timing": "pre_patch",
                    "basis": "Issue and baseline reviewed before patch.",
                },
            }
        )


def test_duplicate_profile_json_keys_are_rejected(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError):
        load_profile(path)
