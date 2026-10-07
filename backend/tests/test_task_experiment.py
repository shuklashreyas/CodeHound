import asyncio
import base64
import json

import pytest
from test_corpus import make_corpus

from codehound.benchmark.task_experiment import (
    TaskProfile,
    compare_observations,
    load_profile,
    observation,
    run_experiment,
    same_json,
)
from codehound.execution.docker import ExecutionResult
from codehound.repositories.checkout import Checkouts


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


@pytest.mark.parametrize("mutate_profile", [False, True])
def test_full_materialization_and_frozen_input_guard(tmp_path, mutate_profile):
    corpus, document = make_corpus(tmp_path)
    adapter = tmp_path / "observe.py"
    adapter.write_text("# Trusted fixture; never executed by this data-only fake runner.\n")
    case = document["cases"][0]
    mapping = tmp_path / "mapping.json"
    payload = {
        "schema_version": 1,
        "name": "Unit experiment",
        "purpose": "development_pilot",
        "adapter": str(adapter),
        "tasks": [
            {
                "task_id": case["task_id"],
                "repository": case["repository"],
                "base_commit": case["base_sha"],
                "issue_sha256": case["issue_sha256"],
                "supported": True,
                "unsupported_reason": None,
                "expected": {"behavior": "good"},
                "authorship": {
                    "scope": "development",
                    "timing": "pre_patch",
                    "basis": "Deterministic unit fixture; not research evidence.",
                },
            }
        ],
    }
    mapping.write_text(json.dumps(payload))
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "x.py").write_text("bad\n")

    class Workspace:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return Checkouts(baseline, baseline, case["base_sha"], case["base_sha"])

        async def __aexit__(self, *args):
            return False

    class DataRunner:
        async def run_container(self, workspace, mounts, command):
            value = (workspace / "x.py").read_text().strip()
            raw = json.dumps({"kind": "returned", "value": {"behavior": value}}).encode()
            token = command[-2]
            if mutate_profile:
                payload["name"] = "changed while running"
                mapping.write_text(json.dumps(payload))
            return ExecutionResult(
                "completed",
                0,
                f"CODEHOUND_CALL_V1:{token}:" + base64.b64encode(raw).decode(),
                "",
                0.01,
                "sha256:" + "1" * 64,
                False,
                False,
                30,
            )

    record = asyncio.run(
        run_experiment(
            corpus,
            mapping,
            adapter,
            "sha256:" + "1" * 64,
            runner=DataRunner(),
            workspace_factory=Workspace,
        )
    )
    row = record["rows"][0]
    if mutate_profile:
        assert record["status"] == "invalidated"
        assert row["decisions"]["task_behavior"] == "abstain"
    else:
        assert record["status"] == "completed"
        assert row["decisions"]["task_behavior"] == "accept"
        assert row["comparison"]["improvements"] == ["behavior"]
