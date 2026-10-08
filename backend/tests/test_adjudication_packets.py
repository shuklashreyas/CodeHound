import hashlib
import io
import json
import urllib.error

import pytest
from test_corpus_run import inputs  # noqa: F401

from codehound.benchmark.adjudication import validate_bundle
from codehound.benchmark.adjudication_packets import (
    MAX_CONTEXT_BYTES,
    attach_neutral,
    collect_case,
    excerpts,
    export,
)
from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.review import blinded_case_id, export_packet


def response(source):
    return io.BytesIO(source.encode())


def test_collector_binds_exact_issue_patch_and_pinned_source(request, tmp_path, monkeypatch):
    fixture_data = request.getfixturevalue("inputs")
    corpus, _, _ = fixture_data
    item = prepare_corpus(corpus)[2][0]
    urls = []

    def fetch(request, timeout):
        urls.append(request.full_url)
        assert timeout == 30
        return response("value = 1\n")

    monkeypatch.setattr("urllib.request.urlopen", fetch)
    result = collect_case(item, tmp_path / "sources")
    validate_bundle(result)
    assert result["case_id"] == blinded_case_id(item)
    assert result["patch_id"] == item.case.id
    assert result["case_identity_sha256"] == item.identity_sha256
    assert result["materials"][0]["content"].encode() == item.issue
    assert result["materials"][1]["content"].encode() == item.patch
    assert result["neutral_evidence"] == []
    assert urls == [f"https://raw.githubusercontent.com/owner/repo/{'b' * 40}/example.py"]
    code = result["materials"][2]["content"]
    assert "1: value = 1" in code
    assert hashlib.sha256(b"value = 1\n").hexdigest() in code
    assert "unit-model" not in json.dumps(result)


def test_missing_context_is_explicit_not_silent_success(request, tmp_path, monkeypatch):
    fixture_data = request.getfixturevalue("inputs")
    item = prepare_corpus(fixture_data[0])[2][0]

    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("missing")

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    result = collect_case(item, tmp_path / "sources")
    assert "unavailable" in result["materials"][2]["content"]
    assert any("Missing baseline context" in reason for reason in result["metadata"]["limitations"])
    assert result["neutral_evidence"] == []


def test_excerpt_retains_hunk_neighborhood_and_explicit_omissions():
    source = "\n".join(f"line-{index}" for index in range(1, 1501))
    patch = (
        "diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n"
        "@@ -900 +900 @@\n-line-900\n+changed\n"
    )
    context = excerpts(source, patch, "example.py")
    assert "1: line-1" in context
    assert "900: line-900" in context
    assert "1500: line-1500" not in context
    assert "... [source lines omitted] ..." in context
    assert len([line for line in context.splitlines() if line[0].isdigit()]) <= 1200


def test_export_checks_original_packets_before_network_or_output(request, tmp_path, monkeypatch):
    fixture_data = request.getfixturevalue("inputs")
    corpus = fixture_data[0]
    packets = tmp_path / "packets"
    export_packet(corpus, packets)
    first = prepare_corpus(corpus)[2][0]
    (packets / blinded_case_id(first) / "patch.diff").write_text("altered")
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("network called")
    )
    destination = tmp_path / "adjudication"
    with pytest.raises(ValueError, match="neutral packet differs"):
        export(corpus, packets, destination)
    assert not destination.exists()


def test_export_ignores_adjacent_evaluator_results_and_prior_reviews(
    request, tmp_path, monkeypatch
):
    fixture_data = request.getfixturevalue("inputs")
    corpus = fixture_data[0]
    packets = tmp_path / "packets"
    export_packet(corpus, packets)
    secret = "EVALUATOR-SECRET-DO-NOT-INCLUDE"
    (packets / "task-report.json").write_text(secret)
    (packets / "ai-review-notes.json").write_text(secret)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response("value = 1\n"))
    result = export(corpus, packets, tmp_path / "adjudication")
    assert secret not in json.dumps(result)
    assert len(result["bundles"]) == 2
    for delivered in result["bundles"]:
        validate_bundle(delivered)


def test_export_refuses_overwrite(request, tmp_path):
    fixture_data = request.getfixturevalue("inputs")
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(ValueError, match="cannot be overwritten"):
        export(fixture_data[0], tmp_path / "packets", destination)


def test_packet_symlink_is_rejected_before_export(request, tmp_path):
    corpus = request.getfixturevalue("inputs")[0]
    packets = tmp_path / "packets"
    export_packet(corpus, packets)
    item = prepare_corpus(corpus)[2][0]
    path = packets / blinded_case_id(item) / "issue.txt"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        export(corpus, packets, tmp_path / "adjudication")


def test_multibyte_giant_source_line_is_bounded_with_explicit_truncation():
    source = "é" * 100_000
    patch = (
        "diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n"
        "@@ -1 +1 @@\n-before\n+after\n"
    )
    context = excerpts(source, patch, "example.py")
    assert len(context.encode()) <= MAX_CONTEXT_BYTES
    assert "[source context truncated at byte limit]" in context


def test_retained_source_symlink_is_not_written_through(request, tmp_path, monkeypatch):
    item = prepare_corpus(request.getfixturevalue("inputs")[0])[2][0]
    root = tmp_path / "sources"
    destination = root / blinded_case_id(item)
    destination.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("do not change")
    (destination / "example.py").symlink_to(outside)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response("value = 1\n"))
    delivered = collect_case(item, root)
    assert outside.read_text() == "do not change"
    assert "unavailable" in delivered["materials"][2]["content"]


@pytest.fixture
def neutral_inputs(request, tmp_path, monkeypatch):
    corpus = request.getfixturevalue("inputs")[0]
    item = prepare_corpus(corpus)[2][0]
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response("value = 1\n"))
    delivered = collect_case(item, tmp_path / "sources")
    source = "print(value)"
    image = "sha256:" + "a" * 64
    run = {
        "status": "completed",
        "exit_code": 0,
        "stdout": "1\n",
        "stderr": "",
        "duration_seconds": 1,
        "image_id": image,
        "output_truncated": False,
        "oom_killed": False,
        "timeout_seconds": 30,
        "network": "none",
        "memory_mb": 512,
        "cpu_limit": 1,
        "command": ["python", "-c", source],
        "source": source,
    }
    artifact = {
        "schema_version": 1,
        "case_id": delivered["case_id"],
        "repository": item.case.repository,
        "base_sha": item.case.base_sha,
        "issue_sha256": item.case.issue_sha256,
        "patch_sha256": item.case.patch_sha256,
        "image_id": image,
        "example": {
            "source": source,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "issue_basis": "Issue reproduction example",
            "adaptations": [],
        },
        "executions": {"baseline": run.copy(), "candidate": run | {"stdout": "2\n"}},
        "gaps": [],
    }
    path = tmp_path / "neutral.json"
    manifest = tmp_path / "manifest.json"

    def save():
        raw = json.dumps(artifact).encode()
        path.write_bytes(raw)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifacts": [
                        {
                            "case_id": delivered["case_id"],
                            "artifact_path": str(path),
                            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                            "execution_roles": list(artifact["executions"]),
                            "gaps": artifact["gaps"],
                        }
                    ],
                }
            )
        )

    save()
    return delivered, artifact, path, manifest, save


def test_attach_binds_raw_runtime_bytes_and_rehashes_material(neutral_inputs):
    delivered, _, path, manifest, _ = neutral_inputs
    result = attach_neutral([delivered], manifest)[0]
    evidence = result["neutral_evidence"][0]
    assert evidence["status"] == "completed"
    assert evidence["content"].encode() == path.read_bytes()
    assert evidence["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["material_sha256"] != delivered["material_sha256"]
    assert delivered["neutral_evidence"] == []
    validate_bundle(result)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda artifact: artifact.update(case_id="foreign-case"),
        lambda artifact: artifact.update(patch_sha256="f" * 64),
        lambda artifact: artifact.update(base_sha="f" * 40),
        lambda artifact: artifact.update(codehound_verdict="accept"),
        lambda artifact: artifact["executions"]["candidate"].update(probe_results=[]),
        lambda artifact: artifact["executions"]["candidate"].update(network="bridge"),
    ],
)
def test_attach_rejects_foreign_identity_or_leaking_fields(neutral_inputs, mutation):
    delivered, artifact, _, manifest, save = neutral_inputs
    mutation(artifact)
    save()
    with pytest.raises(ValueError):
        attach_neutral([delivered], manifest)


def test_attach_rejects_stale_artifact_hash(neutral_inputs):
    delivered, _, path, manifest, _ = neutral_inputs
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="retained manifest hash"):
        attach_neutral([delivered], manifest)


@pytest.mark.parametrize(
    "changes",
    [
        {"exit_code": 1},
        {"status": "timeout"},
        {"oom_killed": True},
        {"output_truncated": True},
    ],
)
def test_execution_or_resource_failure_is_unavailable_not_completed(neutral_inputs, changes):
    delivered, artifact, _, manifest, save = neutral_inputs
    artifact["executions"]["candidate"].update(changes)
    save()
    result = attach_neutral([delivered], manifest)[0]
    assert result["neutral_evidence"][0]["status"] == "unavailable"


def test_checkout_failure_gap_retains_optional_type_as_unavailable(neutral_inputs):
    delivered, artifact, _, manifest, save = neutral_inputs
    artifact["executions"] = {}
    artifact["gaps"] = [
        {
            "stage": "checkout",
            "type": "ValueError",
            "detail": "Checkout contains a symlink.",
        }
    ]
    save()
    result = attach_neutral([delivered], manifest)[0]
    evidence = result["neutral_evidence"][0]
    assert evidence["status"] == "unavailable"
    assert json.loads(evidence["content"])["gaps"][0]["type"] == "ValueError"
