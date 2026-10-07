"""Data-only workspace safeguards and explicit live mutation evidence checks."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from codehound.execution.real_repository_experiment import (
    BASE,
    HEAD,
    VARIANTS,
    apply_operator_patch,
    copy_revision,
    run_experiment,
)
from codehound.repositories.checkout import CheckoutFailure

FIXTURE = Path(__file__).parents[1] / "fixtures" / "real_repository" / "packaging-pr-925"


def snapshot():
    return json.loads((FIXTURE / "snapshot.json").read_text())


def test_copy_is_bounded_data_only_and_excludes_git_metadata(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").write_text("gitdir: /private/host/location")
    (source / "subject.py").write_text("raise RuntimeError('never import me')\n")
    copied = tmp_path / "copy"
    first = copy_revision(source, copied)
    assert (copied / "subject.py").read_bytes() == (source / "subject.py").read_bytes()
    assert not (copied / ".git").exists()
    assert copy_revision(source, tmp_path / "copy-again") == first
    with pytest.raises(ValueError, match="limit"):
        copy_revision(source, tmp_path / "too-large", max_bytes=3)
    with pytest.raises(ValueError, match="limit"):
        copy_revision(source, tmp_path / "too-many", max_files=0)


def test_revision_symlinks_cannot_escape_into_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "outside.py").symlink_to(Path(__file__))
    with pytest.raises(ValueError, match="symlink"):
        copy_revision(source, tmp_path / "copy")


def test_operator_patches_are_explicit_narrow_mutations_and_reject_other_paths(tmp_path):
    for name in ("overfit", "regressive"):
        raw = (FIXTURE / "mutations" / f"{name}.patch").read_bytes()
        assert raw.count(b"diff --git ") == 1
        assert raw.startswith(b"diff --git a/src/packaging/utils.py b/src/packaging/utils.py\n")
        assert len(raw) < 32768
    assert [item[1] for item in VARIANTS] == ["valid", "invalid", "invalid"]
    assert all("operator" in reason.lower() for _, _, reason in VARIANTS)
    outside = tmp_path / "outside.patch"
    outside.write_text("diff --git a/../outside.py b/../outside.py\n")
    with pytest.raises(ValueError, match="configured source"):
        apply_operator_patch(tmp_path, outside)
    link = tmp_path / "link.patch"
    link.symlink_to(FIXTURE / "mutations" / "overfit.patch")
    with pytest.raises(ValueError, match="regular file"):
        apply_operator_patch(tmp_path, link)


def test_unknown_upstream_identity_is_rejected_before_checkout():
    altered = snapshot() | {"base_sha": "f" * 40}
    with pytest.raises(ValueError, match="pinned public"):
        asyncio.run(run_experiment(altered, FIXTURE / "mutations", "sha256:" + "a" * 64))


def test_setup_failure_keeps_a_truthful_partial_checkpoint(monkeypatch):
    recorded = []

    class Unavailable:
        def __init__(self, repository, baseline, candidate):
            assert repository == "pypa/packaging" and baseline == BASE and candidate == HEAD

        async def __aenter__(self):
            raise CheckoutFailure("Public pinned revisions unavailable.")

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr("codehound.execution.real_repository_experiment.GitWorkspace", Unavailable)
    artifact = asyncio.run(
        run_experiment(
            snapshot(),
            FIXTURE / "mutations",
            "sha256:" + "a" * 64,
            checkpoint=lambda value: recorded.append(json.loads(json.dumps(value))),
        )
    )
    assert recorded[0]["status"] == "running" and recorded[0]["benchmark"] is None
    assert recorded[-1]["status"] == "invalidated"
    assert artifact["benchmark"] is None and artifact["mutations"] == []
    assert "unavailable" in artifact["error"]


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID")
    or os.getenv("CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS") != "1",
    reason="Explicit public Git checkout opt-in and a trusted Docker image required",
)
def test_real_repository_mutants_pass_visible_and_existing_tests_but_fail_independent_cases():
    artifact = asyncio.run(
        run_experiment(
            snapshot(),
            FIXTURE / "mutations",
            os.environ["CODEHOUND_TEST_IMAGE_ID"],
        )
    )
    assert artifact["status"] == "completed", artifact
    assert artifact["experiment_kind"] == "synthetic_mutations_on_real_public_repository"
    assert artifact["upstream"]["authorship_method"] == "unknown"
    rows = {row["candidate_id"]: row for row in artifact["benchmark"]["rows"]}
    assert rows["upstream-correct"]["decisions"] == {
        "visible_only": "accept",
        "independent": "accept",
    }
    for name, failed in (("overfit", 2), ("regressive", 1)):
        row = rows[name]
        assert row["decisions"] == {"visible_only": "accept", "independent": "reject"}
        hidden = row["suites"]["hidden"]["candidate"]
        assert sum(case["outcome"] == "failed" for case in hidden["test_report"]["tests"]) == failed
        assert hidden["evidence_source"] == "external_json_assertions"
        assert len(hidden["case_evidence"]) == 13
    assert rows["overfit"]["assessment"]["verdict"] == "incomplete"
    assert rows["regressive"]["assessment"]["verdict"] == "regression_detected"
    for row in rows.values():
        repository = row["repository_tests"]
        assert repository["status"] == "completed", repository
        assert repository["comparison"] == "no_behavior_change_observed", repository
        assert repository["provenance"]["baseline_sha"] == BASE
        assert repository["affects_assessment"] is False
        for revision in ("baseline", "candidate"):
            assert repository[revision]["exit_code"] == 0
            assert len(repository[revision]["test_report"]["tests"]) == 54
    metrics = artifact["benchmark"]["metrics"]["visible_test_passing_candidates"]
    assert metrics["visible_only"]["invalid_detected"] == 0
    assert (
        metrics["independent"]["invalid_detected"] == metrics["independent"]["invalid_total"] == 2
    )
    assert metrics["independent"]["valid_rejected"] == 0
    for mutation in artifact["mutations"]:
        assert mutation["workspace_sha256"] == rows[mutation["candidate_id"]]["workspace_sha256"]
        if mutation["candidate_id"] == "upstream-correct":
            assert mutation["origin"] == "upstream_patch"
        if mutation["origin"] == "operator_synthetic_mutation":
            assert mutation["upstream_commit_sha"] is None
            assert mutation["mutation_base_sha"] == BASE
            raw = (FIXTURE / "mutations" / f"{mutation['candidate_id']}.patch").read_bytes()
            assert mutation["patch_sha256"] == hashlib.sha256(raw).hexdigest()
