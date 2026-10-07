import hashlib
import json

import pytest

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.import_swebench import import_predictions, read_input, records


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_inputs(root, *, reverse=False, outcomes=False):
    root.mkdir(parents=True)
    tasks, predictions = [], []
    for identifier, repo in (("aa__repo-2", "aa/repo"), ("zz__repo-1", "zz/repo"),
                             ("aa__repo-1", "aa/repo"), ("zz__repo-2", "zz/repo")):
        tasks.append({
            "instance_id": identifier, "repo": repo, "base_commit": "a" * 40,
            "problem_statement": "Exact issue text\r\nwith no trailing newline",
            "patch": "GOLD PATCH MUST NOT BE COPIED", "test_patch": "GOLD TESTS",
            "FAIL_TO_PASS": "PRIVATE REFERENCE TEST IDS",
        })
        predictions.append({
            "instance_id": identifier, "model_name_or_path": "reported-model",
            "model_patch": "diff --git a/a.py b/a.py\n+return 1\n",
            "resolved": outcomes, "test_results": {"passed": outcomes},
        })
    predictions.extend([
        {"instance_id": "empty", "model_patch": "\n  "},
        {"instance_id": "no-task", "model_patch": "diff unknown"},
    ])
    if reverse:
        predictions.reverse()
        tasks.reverse()
    paths = {}
    for key, value in (("tasks", tasks), ("predictions", predictions)):
        raw = b"".join((json.dumps(row) + "\n").encode() for row in value)
        path = root / (key + ".jsonl")
        path.write_bytes(raw)
        paths[key] = path
        paths[key + "_source"] = {
            "id": key, "url": f"https://example.org/pinned/{key}.jsonl",
            "revision": "b" * 40, "sha256": sha(raw),
        }
    return paths


def run_import(paths, output, **options):
    return import_predictions(
        paths["predictions"], paths["tasks"], output,
        predictions_source=paths["predictions_source"], tasks_source=paths["tasks_source"],
        agent="upstream-agent", count=3, **options,
    )


def test_round_robin_is_order_and_outcome_independent_without_reference_leaks(tmp_path):
    first = run_import(write_inputs(tmp_path / "in-one"), tmp_path / "out-one")
    second = run_import(
        write_inputs(tmp_path / "in-two", reverse=True, outcomes=True), tmp_path / "out-two"
    )
    one, _, prepared_one = prepare_corpus(first)
    two, _, prepared_two = prepare_corpus(second)
    expected = ["aa__repo-1", "zz__repo-1", "aa__repo-2"]
    assert [case.id for case in one.cases] == [case.id for case in two.cases] == expected
    assert all(case.split == "development" for case in one.cases)
    assert all(case.generation.model == "reported-model" for case in one.cases)
    assert [case.issue for case in prepared_one] == [case.issue for case in prepared_two]
    assert prepared_one[0].issue == b"Exact issue text\r\nwith no trailing newline"
    assert all(case.patch.endswith(b"\n") for case in prepared_one)
    provenance = json.loads((first.parent / "import-provenance.json").read_text())
    assert provenance["excluded"] == {"empty_patch": 1, "missing_task": 1}
    assert provenance["eligible_nonempty_matching_count"] == 4
    assert provenance["eligible_not_selected"] == 1
    for path in first.parent.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert b"GOLD PATCH" not in raw and b"GOLD TESTS" not in raw
            assert b"PRIVATE REFERENCE TEST IDS" not in raw
            assert b"test_results" not in raw


def test_raw_parquet_and_normalized_metadata_hashes_are_distinct(tmp_path):
    paths = write_inputs(tmp_path / "inputs")
    original = tmp_path / "source.parquet"
    original.write_bytes(b"RAW PARQUET FIXTURE")
    paths["tasks_source"]["url"] = "https://example.org/pinned/tasks.parquet"
    paths["tasks_source"]["sha256"] = sha(original.read_bytes())
    with pytest.raises(ValueError, match="Task source SHA256"):
        run_import(paths, tmp_path / "missing-original")
    manifest = run_import(paths, tmp_path / "output", tasks_original_path=original)
    corpus, _, _ = prepare_corpus(manifest)
    assert corpus.sources[0].sha256 == sha(original.read_bytes())
    provenance = json.loads((manifest.parent / "import-provenance.json").read_text())
    assert provenance["normalized_tasks_sha256"] == sha(paths["tasks"].read_bytes())
    assert not provenance["normalization_semantics_verified_by_importer"]


def test_additional_attribution_source_is_checked_bound_and_retained(tmp_path):
    paths = write_inputs(tmp_path / "inputs")
    metadata = tmp_path / "metadata.yaml"
    metadata.write_bytes(b"agent: upstream-agent\nversion: 1\n")
    source = {"id": "agent-metadata", "url": "https://example.org/pin/metadata.yaml",
              "revision": "c" * 40, "sha256": sha(metadata.read_bytes())}
    manifest = run_import(paths, tmp_path / "out", additional_sources=[(source, metadata)])
    corpus, _, _ = prepare_corpus(manifest)
    assert len(corpus.sources) == 3
    assert (manifest.parent / "sources/agent-metadata.txt").read_bytes() == metadata.read_bytes()
    metadata.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Additional source SHA256"):
        run_import(paths, tmp_path / "bad", additional_sources=[(source, metadata)])
    assert not (tmp_path / "bad").exists()


def test_hash_mismatch_and_existing_output_cannot_overwrite(tmp_path):
    paths = write_inputs(tmp_path / "inputs")
    paths["predictions_source"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Prediction source SHA256"):
        run_import(paths, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep").write_text("keep")
    with pytest.raises(ValueError, match="already exists"):
        run_import(paths, output)
    assert (output / "keep").read_text() == "keep"


def test_insufficient_eligible_rows_and_wrong_model_fail_before_writing(tmp_path):
    paths = write_inputs(tmp_path / "inputs")
    with pytest.raises(ValueError, match="Only 4 eligible"):
        import_predictions(
            paths["predictions"], paths["tasks"], tmp_path / "out",
            predictions_source=paths["predictions_source"], tasks_source=paths["tasks_source"],
            agent="agent", count=5,
        )
    with pytest.raises(ValueError, match="model differs"):
        run_import(paths, tmp_path / "wrong-model", model="wrong-attribution")
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "wrong-model").exists()


def test_duplicate_rows_keys_and_invalid_text_are_rejected():
    with pytest.raises(ValueError, match="Duplicate instance_id"):
        records(b'[{"instance_id":"one"},{"instance_id":"one"}]')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        records(b'{"instance_id":"one","instance_id":"two"}')
    with pytest.raises(ValueError):
        records(b'{"instance_id":"one","model_patch":"\\ud800"}')


def test_import_inputs_reject_special_files_and_links(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"[]")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError):
        read_input(link)
    with pytest.raises(ValueError):
        read_input(tmp_path)
