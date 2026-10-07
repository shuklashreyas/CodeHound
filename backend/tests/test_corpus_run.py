import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.corpus_metrics import score, score_evidence, score_group, validate_evidence
from codehound.benchmark.corpus_run import (
    EVALUATORS,
    copy_revision,
    patch_files,
    prepare_mapping,
    run_corpus,
)
from codehound.execution.docker import CONTROLLERS as DOCKER_CONTROLLERS
from codehound.execution.docker import ExecutionResult
from codehound.execution.independent import CONTROLLERS as INDEPENDENT_CONTROLLERS
from codehound.execution.independent import judge
from codehound.execution.provenance import controller_binding
from codehound.repositories.checkout import Checkouts

IMAGE = "sha256:" + "a" * 64
PATCH = (
    b"diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n"
    b"@@ -1 +1 @@\n-value = 1\n+value = 2\n"
)


@pytest.fixture
def inputs(tmp_path):
    issue = b"Return the corrected value while preserving compatibility.\n"
    (tmp_path / "issue.txt").write_bytes(issue)
    (tmp_path / "patch.diff").write_bytes(PATCH)
    case = {
        "id": "supported",
        "task_id": "repo-1",
        "family": "repo",
        "split": "development",
        "repository": "owner/repo",
        "base_sha": "b" * 40,
        "issue_path": "issue.txt",
        "issue_sha256": hashlib.sha256(issue).hexdigest(),
        "patch_path": "patch.diff",
        "patch_sha256": hashlib.sha256(PATCH).hexdigest(),
        "generation": {
            "kind": "published_agent_prediction",
            "model": "unit-model",
            "agent": "unit-agent",
            "source_id": "fixture",
        },
    }
    corpus = {
        "schema_version": 1,
        "name": "unit corpus",
        "purpose": "development_pilot",
        "selection": "Deterministic unit fixtures, never research evidence.",
        "sources": [
            {
                "id": "fixture",
                "url": "https://example.test/predictions",
                "revision": "fixture-v1",
                "sha256": "c" * 64,
            }
        ],
        "cases": [case, case | {"id": "unsupported", "task_id": "repo-2"}],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    suite = {
        "name": "probe",
        "module": "example",
        "function": "answer",
        "cases": [{"id": "one", "expect": {"kind": "value", "value": 2}}],
    }
    config = {
        "schema_version": 1,
        "tasks": [
            {
                "task_id": "repo-1",
                "repository": "owner/repo",
                "base_commit": "b" * 40,
                "issue_sha256": case["issue_sha256"],
                "visible": suite,
                "independent": suite | {"name": "independent"},
                "repository_tests": None,
            }
        ],
    }
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps(config))
    baseline = tmp_path / "repository"
    baseline.mkdir()
    (baseline / "example.py").write_text("value = 1\n")

    class Workspace:
        def __init__(self, repository, base, head):
            assert (repository, base, head) == ("owner/repo", "b" * 40, "b" * 40)

        async def __aenter__(self):
            return Checkouts(baseline, baseline, "b" * 40, "b" * 40)

        async def __aexit__(self, *args):
            return False

    return path, mapping, Workspace


class DataRunner:
    async def run(self, workspace, suite):
        # Read source as data. No repository code is imported or executed on the host.
        value = int((workspace / "example.py").read_text().split("=")[1])
        raw = ExecutionResult(
            "completed",
            0,
            "",
            "",
            0.01,
            IMAGE,
            False,
            False,
            10,
            evidence_source="external_json_assertions",
            call_response={"kind": "returned", "value": value},
            controller_binding=controller_binding(DOCKER_CONTROLLERS).to_dict(),
        )
        tests, observations = [], []
        for case in suite.cases:
            outcome, message = judge(case, raw)
            tests.append(
                {
                    "nodeid": suite.name + "::" + case.id,
                    "outcome": outcome,
                    "duration_seconds": raw.duration_seconds,
                    "message": message,
                }
            )
            observations.append({"case_id": case.id, "observation": raw.to_dict()})
        code = int(any(test["outcome"] != "passed" for test in tests))
        binding = controller_binding(INDEPENDENT_CONTROLLERS).to_dict()
        adapter = Path(__file__).parents[1] / "src/codehound/execution/adapters/call_adapter.py"
        evaluator = hashlib.sha256(
            binding["sha256"].encode() + adapter.read_bytes() + suite.canonical_bytes()
        ).hexdigest()
        return ExecutionResult(
            "completed",
            code,
            "",
            "",
            0.01,
            IMAGE,
            False,
            False,
            10,
            test_report={
                "schema_version": 1,
                "exit_code": code,
                "collected": [test["nodeid"] for test in tests],
                "tests": tests,
                "collection_errors": [],
            },
            case_evidence=observations,
            evaluator_sha256=evaluator,
            evidence_source="external_json_assertions",
            controller_binding=binding,
        )


async def static_clean(snapshot, checkouts, image_id):
    harness = Path(__file__).parents[1] / "src/codehound/execution"
    evaluator = hashlib.sha256(
        (harness / "static_analysis.py").read_bytes()
        + (harness / "inspection/static_ruff.py").read_bytes()
    ).hexdigest()
    return {
        "status": "completed",
        "image_id": image_id,
        "evaluator_sha256": evaluator,
        "coverage": {"comparison_complete": True},
        "new": [],
        "resolved": [],
        "existing": [],
        "counts": {"new": 0, "resolved": 0, "existing": 0},
    }


def run(inputs, **kwargs):
    corpus, mapping, workspace = inputs
    return asyncio.run(
        run_corpus(
            corpus,
            mapping,
            IMAGE,
            runner=DataRunner(),
            workspace_factory=workspace,
            static_analyzer=static_clean,
            **kwargs,
        )
    )


def validate(record, inputs):
    corpus, mapping, _ = inputs
    _, digest, prepared = prepare_corpus(corpus)
    config, config_digest = prepare_mapping(mapping)
    return validate_evidence(record, digest, prepared, config, config_digest)


def test_all_rows_retained_decisions_reconstructed(inputs):
    record = run(inputs)
    assert record["status"] == "completed"
    assert [row["status"] for row in record["rows"]] == ["evaluated", "unsupported"]
    assert record["rows"][0]["decisions"] == dict(
        visible_only="accept",
        independent="accept",
        repository_tests_only="abstain",
        static_only="accept",
    )
    assert set(record["rows"][1]["decisions"].values()) == {"abstain"}
    assert len(validate(record, inputs)) == 2


@pytest.mark.parametrize(
    "mutation",
    ["controller", "image", "workspace", "outcome", "observation", "missing_row", "repo"],
)
def test_scoring_rejects_tampered_evidence(inputs, mutation):
    record = run(inputs)
    row = record["rows"][0]
    if mutation == "controller":
        record["controller_binding"]["sha256"] = "f" * 64
    elif mutation == "image":
        record["image_id"] = "python:latest"
    elif mutation == "workspace":
        row["candidate_workspace_sha256"] = "not-a-hash"
    elif mutation == "repo":
        row["repository"] = "wrong/repo"
    elif mutation == "missing_row":
        record["rows"].pop()
    elif mutation == "outcome":
        row["suites"]["hidden"]["candidate"]["test_report"]["tests"][0]["outcome"] = "failed"
    else:
        row["suites"]["hidden"]["candidate"]["case_evidence"][0]["observation"]["call_response"][
            "value"
        ] = 123
    with pytest.raises(ValueError):
        validate(record, inputs)


def test_nested_timeout_is_not_global_crash(inputs):
    async def timeout(*args):
        raise TimeoutError("local setup timeout")

    corpus, mapping, workspace = inputs
    record = asyncio.run(
        run_corpus(
            corpus,
            mapping,
            IMAGE,
            runner=DataRunner(),
            workspace_factory=workspace,
            static_analyzer=timeout,
        )
    )
    assert record["status"] == "completed"
    assert record["rows"][0]["status"] == "not_run"
    assert record["rows"][0]["assessment"] is None
    assert set(record["rows"][0]["decisions"].values()) == {"abstain"}
    validate(record, inputs)


def test_optional_stage_deadline_preserves_independent_evidence(inputs):
    async def stall(*args):
        await asyncio.sleep(10)

    corpus, mapping, workspace = inputs
    record = asyncio.run(
        run_corpus(
            corpus,
            mapping,
            IMAGE,
            max_seconds=1,
            runner=DataRunner(),
            workspace_factory=workspace,
            static_analyzer=stall,
        )
    )
    assert record["status"] == "timeout"
    assert record["rows"][0]["status"] == "interrupted"
    assert record["rows"][0]["decisions"]["independent"] == "accept"
    assert record["rows"][1]["status"] == "not_run"
    validate(record, inputs)


def test_mapping_identity_mismatch_invalidates_all(inputs):
    path = inputs[1]
    mapping = json.loads(path.read_text())
    mapping["tasks"][0]["issue_sha256"] = "f" * 64
    path.write_text(json.dumps(mapping))
    record = run(inputs)
    assert record["status"] == "invalidated"
    assert all(set(row["decisions"].values()) == {"abstain"} for row in record["rows"])


def test_workspace_mutation_invalidates_all(inputs):
    async def mutate(snapshot, checkouts, image):
        (checkouts.candidate / "example.py").write_text("value = 99\n")
        return await static_clean(snapshot, checkouts, image)

    corpus, mapping, workspace = inputs
    record = asyncio.run(
        run_corpus(
            corpus,
            mapping,
            IMAGE,
            runner=DataRunner(),
            workspace_factory=workspace,
            static_analyzer=mutate,
        )
    )
    assert record["status"] == "invalidated"
    assert all(row["assessment"] is None for row in record["rows"])


@pytest.mark.parametrize(
    "patch",
    [
        PATCH.replace(b"example.py", b"../escape.py"),
        PATCH.replace(b"example.py", b".git/config"),
        PATCH.replace(b"--- a/example.py", b"old mode 100644\nnew mode 100755\n--- a/example.py"),
        PATCH.replace(b"--- a/example.py", b"new file mode 120000\n--- a/example.py"),
        PATCH.replace(b"--- a/example.py", b"new file mode 160000\n--- a/example.py"),
        b"diff --git a/x b/x\nGIT binary patch\n",
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_unsafe_patch_rejected_before_execution(patch):
    with pytest.raises(ValueError):
        patch_files(patch)


def test_copy_rejects_symlink(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        copy_revision(original, tmp_path / "copy")


def test_abstentions_are_in_denominators_unknown_excluded_and_no_unsupported_assessment():
    rows = []
    for label, decision in [
        ("invalid", "reject"),
        ("invalid", "accept"),
        ("invalid", "abstain"),
        ("valid", "reject"),
        ("valid", "accept"),
        ("valid", "abstain"),
        ("unreviewed", "abstain"),
    ]:
        rows.append(
            {
                "accuracy_eligible": label != "unreviewed",
                "label": label,
                "status": "unsupported" if decision == "abstain" else "evaluated",
                "assessment": {"verdict": "regression_detected"},
                "decisions": dict.fromkeys(EVALUATORS, decision),
            }
        )
    result = score(rows, "independent")
    assert result["invalid_denominator"] == result["valid_denominator"] == 3
    assert result["bad_patches_caught"] == result["bad_patches_missed"] == 1
    assert result["false_positives"] == 1
    assert result["invalid_abstentions"] == result["valid_abstentions"] == 1
    assert result["excluded_unknown_or_ambiguous"] == 1
    assert result["bad_patch_detection_rate"] == pytest.approx(1 / 3)
    assert score_group(rows)["incomplete_or_regressive"]["regressions"] == 4


def test_posthoc_empty_reviews_overrides_execution_labels(inputs, tmp_path):
    record = run(inputs)
    for row in record["rows"]:
        row.update(label="invalid", accuracy_eligible=True, review_status="agreed")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(record))
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps({"schema_version": 1, "reviews": []}))
    result = score_evidence(evidence, inputs[0], inputs[1], reviews)
    assert all(row["label"] == "unreviewed" for row in result["rows"])
    assert result["metrics"]["all_cases"]["independent"]["reviewed_cases"] == 0
    assert result["metrics"]["all_cases"]["independent"]["invalid_denominator"] == 0


def test_ambiguous_reviews_excluded_supported_and_unsupported_denominators(inputs, tmp_path):
    record = run(inputs)
    evidence = tmp_path / "review-evidence.json"
    evidence.write_text(json.dumps(record))
    _, _, prepared = prepare_corpus(inputs[0])
    common = {
        "schema_version": 1,
        "reviewed_at": "2026-10-07T19:00:00+00:00",
        "failure_categories": [],
        "rationale": "Unit fixture review record for testing joins.",
        "attestation": "I personally reviewed this patch against the task.",
    }
    records = [
        common
        | {
            "case_id": prepared[0].case.id,
            "case_identity_sha256": prepared[0].identity_sha256,
            "reviewer": "reviewer-1",
            "verdict": "valid",
        },
        common
        | {
            "case_id": prepared[0].case.id,
            "case_identity_sha256": prepared[0].identity_sha256,
            "reviewer": "reviewer-2",
            "verdict": "uncertain",
        },
        common
        | {
            "case_id": prepared[1].case.id,
            "case_identity_sha256": prepared[1].identity_sha256,
            "reviewer": "reviewer-1",
            "verdict": "invalid",
            "failure_categories": ["incomplete_fix"],
        },
    ]
    reviews = tmp_path / "ambiguous-reviews.json"
    reviews.write_text(json.dumps({"schema_version": 1, "reviews": records}))
    result = score_evidence(evidence, inputs[0], inputs[1], reviews)
    metric = result["metrics"]["all_cases"]["independent"]
    assert metric["reviewed_cases"] == metric["invalid_denominator"] == 1
    assert metric["invalid_abstentions"] == 1
    assert metric["valid_denominator"] == 0
    assert metric["excluded_unknown_or_ambiguous"] == 1
    assert metric["bad_patch_detection_rate"] == 0
    assert result["rows"][0]["review_status"] == "uncertain"


def test_cancellation_propagates_with_partial_checkpoint(inputs):
    saved = []
    entered = asyncio.Event()

    async def stall(*args):
        entered.set()
        await asyncio.sleep(10)

    async def exercise():
        corpus, mapping, workspace = inputs
        task = asyncio.create_task(
            run_corpus(
                corpus,
                mapping,
                IMAGE,
                runner=DataRunner(),
                workspace_factory=workspace,
                static_analyzer=stall,
                checkpoint=lambda record: saved.append(json.loads(json.dumps(record))),
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert saved[-1]["status"] == "cancelled"
    assert saved[-1]["rows"][0]["status"] == "interrupted"
    assert saved[-1]["rows"][0]["decisions"]["independent"] == "accept"
    assert saved[-1]["rows"][1]["status"] == "not_run"
    validate(saved[-1], inputs)


def test_mapping_rejects_boolean_version(inputs):
    path = inputs[1]
    config = json.loads(path.read_text())
    config["schema_version"] = True
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        prepare_mapping(path)


def test_full_corpus_docker_execution_and_posthoc_empty_reviews(inputs, tmp_path):
    image = os.getenv("CODEHOUND_TEST_IMAGE_ID")
    if not image:
        pytest.skip("Trusted Docker image not configured")
    corpus, mapping, workspace = inputs
    baseline = tmp_path / "repository/example.py"
    baseline.write_text("value = 1\n\ndef answer():\n    return value\n")
    live_patch = (
        b"diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n"
        b"@@ -1,4 +1,4 @@\n-value = 1\n+value = 2\n \n def answer():\n     return value\n"
    )
    (tmp_path / "patch.diff").write_bytes(live_patch)
    payload = json.loads(corpus.read_text())
    for case in payload["cases"]:
        case["patch_sha256"] = hashlib.sha256(live_patch).hexdigest()
    corpus.write_text(json.dumps(payload))
    evidence = asyncio.run(run_corpus(corpus, mapping, image, workspace_factory=workspace))
    assert evidence["status"] == "completed"
    supported, unsupported = evidence["rows"]
    assert supported["status"] == "evaluated"
    assert supported["decisions"] == {
        "visible_only": "accept",
        "independent": "accept",
        "repository_tests_only": "abstain",
        "static_only": "accept",
    }
    assert unsupported["status"] == "unsupported"
    assert (
        supported["suites"]["hidden"]["baseline"]["test_report"]["tests"][0]["outcome"] == "failed"
    )
    assert (
        supported["suites"]["hidden"]["candidate"]["test_report"]["tests"][0]["outcome"] == "passed"
    )
    path = tmp_path / "docker-evidence.json"
    path.write_text(json.dumps(evidence))
    reviews = tmp_path / "docker-reviews.json"
    reviews.write_text(json.dumps({"schema_version": 1, "reviews": []}))
    scored = score_evidence(path, corpus, mapping, reviews)
    metric = scored["metrics"]["all_cases"]["independent"]
    assert metric["total_cases"] == 2
    assert metric["reviewed_cases"] == 0
    assert metric["all_case_decision_coverage"] == 0.5
    assert metric["bad_patch_detection_rate"] is None


def test_scoring_rejects_mixed_producer_metadata(inputs):
    record = run(inputs)
    record["rows"][0]["generation"]["model"] = "wrong-producer"
    with pytest.raises(ValueError, match="identity"):
        validate(record, inputs)


@pytest.mark.parametrize("status", ["evaluated", "interrupted"])
def test_unrecomputed_assessment_cannot_inflate_regression_counts(inputs, status):
    record = run(inputs)
    row = record["rows"][0]
    row["status"] = status
    row["suites"] = {}
    row["assessment"] = {"verdict": "regression_detected"}
    row["decisions"] = dict.fromkeys(EVALUATORS, "abstain")
    with pytest.raises(ValueError, match="assessment"):
        validate(record, inputs)
