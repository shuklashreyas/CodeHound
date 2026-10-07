import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from test_jobs import HEADERS, artifact, enqueue
from test_jobs import ready as ready

from codehound.evaluation.job_schemas import job_summary
from codehound.evaluation.registry import EvaluationProfile, configured_image, load_profiles
from codehound.execution import worker
from codehound.main import app

GLOBAL_IMAGE = "sha256:" + "a" * 64
PROFILE_IMAGE = "sha256:" + "b" * 64
CHANGED_IMAGE = "sha256:" + "c" * 64


def write_profile(profile, filename="profile.json"):
    path = Path(os.environ["CODEHOUND_PROFILE_DIR"]) / filename
    path.write_text(profile.model_dump_json())


@pytest.mark.parametrize(
    "image",
    [
        "",
        "python:3.13",
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        PROFILE_IMAGE + "\n",
        " " + PROFILE_IMAGE,
        "https://example.com/image",
        123,
        True,
    ],
)
def test_operator_profile_rejects_unpinned_or_malformed_image(image):
    source = load_profiles()["codehound-url-contract"].model_dump(mode="json")
    with pytest.raises(ValueError):
        EvaluationProfile.model_validate(source | {"execution_image_id": image})


def test_image_resolution_preserves_legacy_profiles_and_public_metadata(monkeypatch):
    original = load_profiles()["codehound-url-contract"]
    legacy = original.model_dump(mode="json", exclude={"execution_image_id"})
    default = EvaluationProfile.model_validate(legacy)
    explicit_null = EvaluationProfile.model_validate(legacy | {"execution_image_id": None})
    pinned = EvaluationProfile.model_validate(legacy | {"execution_image_id": PROFILE_IMAGE})
    public = default.public()
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", GLOBAL_IMAGE)
    assert configured_image() == configured_image(default) == configured_image(explicit_null)
    assert configured_image(pinned) == PROFILE_IMAGE
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", "python:latest")
    assert configured_image(default) is None
    assert configured_image(pinned) == PROFILE_IMAGE
    monkeypatch.delenv("CODEHOUND_EXECUTION_IMAGE_ID")
    assert configured_image(default) is None
    assert configured_image(pinned) == PROFILE_IMAGE
    # Historical/public profile projections never consult current deployment config.
    assert default.public() == explicit_null.public() == pinned.public() == public
    assert "execution_configured" not in public and "execution_image_id" not in public


def test_api_mixed_availability_without_global_image(ready, monkeypatch):
    client, identifier, _, profile = ready
    pinned = profile.model_copy(update={"execution_image_id": PROFILE_IMAGE})
    write_profile(pinned)
    write_profile(profile.model_copy(update={"id": "fallback-profile"}), "fallback.json")
    monkeypatch.delenv("CODEHOUND_EXECUTION_IMAGE_ID")
    response = client.get(f"/api/verifications/{identifier}/profiles")
    assert response.status_code == 200
    available = response.json()
    assert available["configured"] is True
    choices = {item["id"]: item for item in available["profiles"]}
    assert choices["test-profile"]["execution_configured"] is True
    assert choices["test-profile"]["execution_image_id"] == PROFILE_IMAGE
    assert choices["fallback-profile"]["execution_configured"] is False
    assert choices["fallback-profile"]["execution_image_id"] is None
    rejected = client.post(
        f"/api/verifications/{identifier}/executions",
        json={"profile_id": "fallback-profile"},
        headers=HEADERS,
    )
    assert rejected.status_code == 503
    accepted = enqueue(ready)
    assert accepted.status_code == 202 and accepted.json()["image_id"] == PROFILE_IMAGE


def test_each_profile_selects_its_image_and_global_fallback(ready):
    client, identifier, store, profile = ready
    write_profile(profile.model_copy(update={"execution_image_id": PROFILE_IMAGE}))
    write_profile(profile.model_copy(update={"id": "fallback-profile"}), "fallback.json")
    pinned = enqueue(ready).json()
    assert pinned["image_id"] == PROFILE_IMAGE
    store.cancel(pinned["id"], 123)
    fallback = client.post(
        f"/api/verifications/{identifier}/executions",
        json={"profile_id": "fallback-profile"},
        headers=HEADERS,
    )
    assert fallback.status_code == 202 and fallback.json()["image_id"] == GLOBAL_IMAGE


def test_api_profile_image_configuration_fails_closed_and_request_cannot_override(ready):
    client, identifier, _, profile = ready
    write_profile(profile.model_copy(update={"execution_image_id": PROFILE_IMAGE}))
    for field in ("image_id", "execution_image_id"):
        response = client.post(
            f"/api/verifications/{identifier}/executions",
            json={"profile_id": "test-profile", field: CHANGED_IMAGE},
            headers=HEADERS,
        )
        assert response.status_code == 422
    path = Path(os.environ["CODEHOUND_PROFILE_DIR"]) / "profile.json"
    raw = json.loads(path.read_text())
    path.write_text(json.dumps(raw | {"execution_image_id": "python:latest"}))
    assert client.get(f"/api/verifications/{identifier}/profiles").status_code == 503
    assert enqueue(ready).status_code == 503


def test_queued_image_and_profile_survive_operator_changes_and_idempotent_retry(ready, monkeypatch):
    client, identifier, store, profile = ready
    original = profile.model_copy(update={"execution_image_id": PROFILE_IMAGE})
    write_profile(original)
    key = str(uuid4())
    queued = enqueue(ready, key)
    assert queued.status_code == 202
    job = store.get(queued.json()["id"], 123)
    assert job.image_id == PROFILE_IMAGE
    assert job.profile_snapshot["execution_image_id"] == PROFILE_IMAGE
    historical = job_summary(job).profile
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", CHANGED_IMAGE)
    write_profile(
        profile.model_copy(
            update={
                "execution_image_id": CHANGED_IMAGE,
                "label": "Changed deployment profile",
            }
        )
    )
    repeated = enqueue(ready, key)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == job.id
    assert repeated.json()["image_id"] == PROFILE_IMAGE
    assert repeated.json()["profile"] == historical == original.public()
    assert client.get(f"/api/executions/{job.id}/export").json()["image_id"] == PROFILE_IMAGE
    current = client.get(f"/api/verifications/{identifier}/profiles").json()["profiles"][0]
    assert current["execution_image_id"] == CHANGED_IMAGE
    assert current["label"] == "Changed deployment profile"
    # Persisted pre-feature snapshots did not contain this optional field.
    legacy_snapshot = dict(job.profile_snapshot)
    legacy_snapshot.pop("execution_image_id")
    job.profile_snapshot = legacy_snapshot
    assert job_summary(job).profile == historical


def test_worker_uses_original_queued_profile_image_after_deployment_changes(ready, monkeypatch):
    _, _, store, profile = ready
    write_profile(profile.model_copy(update={"execution_image_id": PROFILE_IMAGE}))
    assert enqueue(ready).status_code == 202
    claimed = store.claim()
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", CHANGED_IMAGE)
    write_profile(profile.model_copy(update={"execution_image_id": CHANGED_IMAGE}))
    calls = []

    async def docker(*args):
        calls.append(args)
        return 0, b"", b""

    async def evaluate(snapshot, suites, runner, **kwargs):
        assert runner.image_id == PROFILE_IMAGE
        return artifact()

    monkeypatch.setattr(worker, "control", docker)
    result = asyncio.run(worker.execute_job(claimed, app.state.database, executor=evaluate))
    assert calls == [("image", "inspect", PROFILE_IMAGE)]
    assert result["profile"] == profile.public()
