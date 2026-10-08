import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from codehound.benchmark.corpus import (
    Corpus,
    case_identity,
    prepare_corpus,
    read_retained_file,
)


def make_corpus(tmp_path):
    issue = b"Fix the task while preserving existing behavior.\n"
    patch = b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-bad\n+good\n"
    (tmp_path / "issue.txt").write_bytes(issue)
    (tmp_path / "patch.diff").write_bytes(patch)
    payload = {
        "schema_version": 1,
        "name": "Unit pilot",
        "purpose": "development_pilot",
        "selection": "Retained deterministic unit test fixture, not research evidence.",
        "sources": [
            {
                "id": "published",
                "url": "https://example.test/predictions.jsonl",
                "revision": "pinned-version",
                "sha256": "a" * 64,
            }
        ],
        "cases": [
            {
                "id": "producer-case",
                "task_id": "repo-1",
                "family": "repo",
                "split": "development",
                "repository": "owner/repo",
                "base_sha": "b" * 40,
                "issue_path": "issue.txt",
                "issue_sha256": hashlib.sha256(issue).hexdigest(),
                "patch_path": "patch.diff",
                "patch_sha256": hashlib.sha256(patch).hexdigest(),
                "generation": {
                    "kind": "published_agent_prediction",
                    "model": "model-private",
                    "agent": "agent-private",
                    "source_id": "published",
                },
            }
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload))
    return path, payload


def test_prepare_retains_bytes_and_binds_source_provenance(tmp_path):
    path, payload = make_corpus(tmp_path)
    corpus, checksum, prepared = prepare_corpus(path)
    assert len(checksum) == 64
    item = prepared[0]
    assert item.issue == (tmp_path / "issue.txt").read_bytes()
    assert item.patch == (tmp_path / "patch.diff").read_bytes()
    assert item.identity_sha256 == case_identity(item.case, corpus.sources)
    for field in ("url", "revision", "sha256"):
        changed = deepcopy(payload)
        changed["sources"][0][field] = (
            "c" * 64 if field == "sha256" else changed["sources"][0][field] + "new"
        )
        updated = Corpus.model_validate(changed)
        assert case_identity(updated.cases[0], updated.sources) != item.identity_sha256
    # Paths and display IDs can move without stale substantive identities.
    renamed = item.case.model_copy(update={"id": "display-only", "patch_path": "elsewhere"})
    assert case_identity(renamed, corpus.sources) == item.identity_sha256


def test_identity_binds_task_repository_base_artifacts_and_generation(tmp_path):
    path, _ = make_corpus(tmp_path)
    corpus, _, prepared = prepare_corpus(path)
    item = prepared[0]
    for field, value in (
        ("task_id", "other-task"),
        ("repository", "another/repo"),
        ("base_sha", "d" * 40),
        ("issue_sha256", "e" * 64),
        ("patch_sha256", "f" * 64),
    ):
        assert case_identity(item.case.model_copy(update={field: value}), corpus.sources) != (
            item.identity_sha256
        )
    generation = item.case.generation.model_copy(update={"agent": "another-agent"})
    assert case_identity(
        item.case.model_copy(update={"generation": generation}), corpus.sources
    ) != (item.identity_sha256)


@pytest.mark.parametrize("field", ["issue", "patch"])
def test_changed_retained_artifact_fails_closed(tmp_path, field):
    path, _ = make_corpus(tmp_path)
    (tmp_path / ("issue.txt" if field == "issue" else "patch.diff")).write_text("tampered")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        prepare_corpus(path)


def test_contract_rejects_duplicates_split_leakage_and_unknown_sources(tmp_path):
    _, payload = make_corpus(tmp_path)
    for mode in ("id", "identity", "split", "source", "task-state"):
        changed = deepcopy(payload)
        second = deepcopy(changed["cases"][0])
        if mode != "id":
            second["id"] = "different-display-id"
        if mode == "split":
            changed["purpose"] = "evaluation"
            second["split"] = "evaluation"
        if mode == "source":
            second["generation"]["source_id"] = "missing"
        if mode == "task-state":
            second["base_sha"] = "c" * 40
        changed["cases"].append(second)
        with pytest.raises(ValidationError):
            Corpus.model_validate(changed)
    payload["schema_version"] = True
    with pytest.raises(ValidationError):
        Corpus.model_validate(payload)


@pytest.mark.parametrize(
    "relative", ["../outside", "/etc/passwd", "dir/../../outside", "dir\\file"]
)
def test_retained_paths_reject_escape(tmp_path, relative):
    with pytest.raises(ValueError, match="within"):
        read_retained_file(tmp_path, relative, max_bytes=100)


def test_retained_paths_reject_symlinks_and_nonregular_files(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "file").write_text("safe")
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "link-file").symlink_to(tmp_path / "real" / "file")
    for name in ("linked/file", "link-file", "real"):
        with pytest.raises(ValueError, match="regular"):
            read_retained_file(tmp_path, name, max_bytes=100)
    with pytest.raises(ValueError, match="regular"):
        read_retained_file(tmp_path / "linked", "file", max_bytes=100)


def test_retained_file_size_limit_and_manifest_symlink(tmp_path):
    path, _ = make_corpus(tmp_path)
    with pytest.raises(ValueError, match="regular"):
        read_retained_file(tmp_path, "issue.txt", max_bytes=1)
    alias = tmp_path / "linked.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="regular"):
        prepare_corpus(alias)


def test_corpus_rejects_duplicate_json_keys_and_pathological_nesting(tmp_path):
    path, payload = make_corpus(tmp_path)
    path.write_text(
        json.dumps(payload).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        )
    )
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        prepare_corpus(path)
    path.write_text("[" * 2000 + "0" + "]" * 2000)
    with pytest.raises(ValueError, match="nesting"):
        prepare_corpus(path)
