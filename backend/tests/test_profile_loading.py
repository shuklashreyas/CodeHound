import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codehound.evaluation.registry import load_profiles


@pytest.fixture
def profile_directory(tmp_path, monkeypatch):
    directory = tmp_path / "profiles"
    directory.mkdir()
    monkeypatch.setenv("CODEHOUND_PROFILE_DIR", str(directory))
    return directory


def valid_profile():
    source = (
        Path(__file__).parents[1] / "src/codehound/evaluation/profiles/codehound-url-contract.json"
    )
    return source.read_bytes()


def test_regular_profile_load_and_duplicate_inventory_still_work(profile_directory):
    (profile_directory / "profile.json").write_bytes(valid_profile())
    profiles = load_profiles()
    assert list(profiles) == ["codehound-url-contract"]
    assert profiles["codehound-url-contract"].visible.cases
    (profile_directory / "duplicate.json").write_bytes(valid_profile())
    with pytest.raises(ValueError, match="Duplicate"):
        load_profiles()


def test_directories_and_symlink_profile_files_are_rejected(profile_directory):
    path = profile_directory / "profile.json"
    path.mkdir()
    with pytest.raises((ValueError, OSError)):
        load_profiles()
    path.rmdir()
    target = profile_directory / "regular"
    target.write_bytes(valid_profile())
    path.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        load_profiles()


def test_fifo_profile_is_rejected_without_waiting_for_a_writer(profile_directory):
    os.mkfifo(profile_directory / "profile.json")
    source = Path(__file__).parents[1] / "src"
    code = """from codehound.evaluation.registry import load_profiles
try:
    load_profiles()
except (ValueError, OSError):
    print('rejected')
else:
    raise SystemExit('FIFO was accepted')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(source),
            "CODEHOUND_PROFILE_DIR": str(profile_directory),
        },
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0 and result.stdout.strip() == "rejected"


def test_rejected_profile_becomes_unavailable_api_evidence(authenticated_client, monkeypatch):
    from codehound.api import executions

    def rejected_profile():
        raise ValueError("Operator profile is not a regular file.")

    # The actual FIFO read is bounded by the subprocess test above. Keep this
    # error-mapping test safe even if a future file loader blocks again.
    monkeypatch.setattr(executions, "load_profiles", rejected_profile)
    created = authenticated_client.post(
        "/api/verifications",
        json={
            "pr_url": "https://github.com/shuklashreyas/CodeHound/pull/1",
            "issue_text": "Check the public parsing behavior.",
        },
        headers={"X-CodeHound-Request": "1"},
    )
    assert created.status_code == 201
    result = authenticated_client.get(f"/api/verifications/{created.json()['id']}/profiles")
    assert result.status_code == 503
    assert result.json()["detail"] == "Operator test profiles are unavailable."


def test_socket_profile_is_rejected(monkeypatch):
    # macOS Unix-domain socket names are shorter than pytest's default temp path.
    with TemporaryDirectory(prefix="ch-profiles-", dir="/tmp") as temporary:
        directory = Path(temporary)
        monkeypatch.setenv("CODEHOUND_PROFILE_DIR", temporary)
        with socket.socket(socket.AF_UNIX) as channel:
            channel.bind(str(directory / "profile.json"))
            with pytest.raises((ValueError, OSError)):
                load_profiles()


def test_oversize_regular_profile_is_rejected(profile_directory):
    path = profile_directory / "profile.json"
    raw = valid_profile()
    path.write_bytes(raw + b" " * (128 * 1024 + 1 - len(raw)))
    with pytest.raises(ValueError):
        load_profiles()


def test_exact_profile_size_limit_is_supported(profile_directory):
    raw = valid_profile()
    (profile_directory / "profile.json").write_bytes(raw + b" " * (128 * 1024 - len(raw)))
    assert list(load_profiles()) == ["codehound-url-contract"]


def test_inventory_limit_is_rejected_before_any_profile_is_opened(profile_directory, monkeypatch):
    from codehound.evaluation import registry

    for index in range(51):
        (profile_directory / f"{index}.json").write_text("{}")

    def forbidden_open(*args, **kwargs):
        raise AssertionError("Inventory must be bounded before reading profiles")

    monkeypatch.setattr(registry.os, "open", forbidden_open)
    with pytest.raises(ValueError, match="At most 50"):
        load_profiles()


@pytest.mark.parametrize("replacement", ["fifo", "symlink"])
def test_open_time_type_replacement_cannot_bypass_regular_file_validation(
    profile_directory, monkeypatch, replacement
):
    from codehound.evaluation import registry

    selected = profile_directory / "profile.json"
    selected.write_bytes(valid_profile())
    target = profile_directory / "regular"
    target.write_bytes(valid_profile())
    real_open = os.open
    replaced = False

    def swap_then_open(path, flags, *args, **kwargs):
        nonlocal replaced
        # Fail before opening a FIFO if the nonblocking protection regresses.
        assert flags & os.O_NONBLOCK
        if Path(path) == selected and not replaced:
            replaced = True
            selected.unlink()
            if replacement == "fifo":
                os.mkfifo(selected)
            else:
                selected.symlink_to(target)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(registry.os, "open", swap_then_open)
    with pytest.raises((ValueError, OSError)):
        load_profiles()
    assert replaced


def test_profile_growth_after_open_is_capped_before_json_validation(profile_directory, monkeypatch):
    from codehound.evaluation import registry

    path = profile_directory / "profile.json"
    path.write_bytes(valid_profile())
    real_read = os.read
    requests = []

    def growing_file(descriptor, count):
        requests.append(count)
        # Simulate a regular file growing after its initial fstat check.
        return b" " * count

    monkeypatch.setattr(registry.os, "read", growing_file)
    with pytest.raises(ValueError):
        load_profiles()
    assert sum(requests) <= 128 * 1024 + 1
    monkeypatch.setattr(registry.os, "read", real_read)


def test_descriptor_is_closed_after_invalid_json(profile_directory, monkeypatch):
    from codehound.evaluation import registry

    (profile_directory / "profile.json").write_text(json.dumps({"invalid": True}))
    descriptors = []
    real_open = os.open

    def observe_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(registry.os, "open", observe_open)
    with pytest.raises(ValueError):
        load_profiles()
    assert descriptors
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
