"""Resolve the deployment with synthetic settings; no Docker daemon or real secrets."""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codehound.db.auth import SharedAuthStore

ROOT = Path(__file__).resolve().parents[2]
KEY = "CODEHOUND_SESSION_ENCRYPTION_KEY"
LIMITS = {
    "CODEHOUND_MAX_PENDING_PER_ACCOUNT": "7",
    "CODEHOUND_MAX_PENDING_EXECUTIONS": "31",
    "CODEHOUND_MUTATION_REQUEST_LIMIT": "83",
    "CODEHOUND_OAUTH_REQUEST_LIMIT": "13",
    "CODEHOUND_REQUEST_LIMIT_WINDOW_SECONDS": "47",
    "CODEHOUND_REQUEST_LIMIT_MAX_BUCKETS": "4321",
    "CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT": "123",
    "CODEHOUND_MAX_VERIFICATIONS_TOTAL": "2345",
    "CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT": "345",
    "CODEHOUND_MAX_EXECUTIONS_TOTAL": "4567",
}
SYNTHETIC_KEY = base64.urlsafe_b64encode(bytes(32)).decode()


@pytest.fixture
def compose_config(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is not installed")
    config_dir = tmp_path / "docker-config"
    plugins = config_dir / "cli-plugins"
    plugins.mkdir(parents=True)
    # Desktop installs its executable in the user's plugin directory. Link only
    # that executable into an otherwise empty config; never read Docker credentials.
    plugin = Path.home() / ".docker" / "cli-plugins" / "docker-compose"
    if plugin.is_file():
        (plugins / "docker-compose").symlink_to(plugin.resolve())
    isolated_env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(tmp_path),
        "DOCKER_CONFIG": str(config_dir),
    }
    version = subprocess.run(
        [docker, "compose", "version"],
        cwd=tmp_path,
        env=isolated_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if version.returncode:
        pytest.skip("Docker Compose CLI is not installed")
    compose = tmp_path / "compose.yaml"
    compose.write_bytes((ROOT / "compose.yaml").read_bytes())
    # Explicit --env-file must override even a local synthetic default .env.
    (tmp_path / ".env").write_text(f"{KEY}=do-not-load-default-env\n")
    env_file = tmp_path / "selected.env"

    def resolve(settings=None, shell=None):
        env_file.write_text("".join(f"{key}={value}\n" for key, value in (settings or {}).items()))
        result = subprocess.run(
            [
                docker,
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose),
                "config",
                "--format",
                "json",
            ],
            cwd=tmp_path,
            env=isolated_env | (shell or {}),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["services"]["api"]["environment"]

    return resolve


@pytest.mark.parametrize("source", ["env-file", "shell"])
def test_compose_forwards_every_operator_limit(compose_config, source):
    selected = LIMITS | {"UNRELATED_TEST_SECRET": "must-not-be-forwarded"}
    environment = compose_config(
        selected if source == "env-file" else {}, selected if source == "shell" else {}
    )
    assert {key: environment[key] for key in LIMITS} == LIMITS
    assert "UNRELATED_TEST_SECRET" not in environment


def test_empty_shell_settings_override_configured_env_file(compose_config):
    configured = LIMITS | {KEY: SYNTHETIC_KEY}
    explicitly_empty = dict.fromkeys(configured, "")
    environment = compose_config(configured, explicitly_empty)
    assert {key: environment[key] for key in configured} == explicitly_empty


def test_unset_compose_settings_leave_application_defaults(compose_config, monkeypatch):
    environment = compose_config()
    # Compose's normalized model retains null for an unresolved bare entry;
    # it omits that variable from the container environment, rather than sending "".
    for key in [KEY, *LIMITS]:
        assert environment.get(key) is None
        monkeypatch.delenv(key, raising=False)
    from codehound.api.github import memory_store
    from codehound.main import app

    with TestClient(app):
        assert app.state.auth_store is memory_store


@pytest.mark.parametrize("source", ["env-file", "shell"])
def test_compose_preserves_explicit_empty_settings(compose_config, monkeypatch, source):
    settings = dict.fromkeys([KEY, *LIMITS], "")
    environment = compose_config(
        settings if source == "env-file" else {}, settings if source == "shell" else {}
    )
    assert {key: environment[key] for key in settings} == settings
    # Test the empty key from resolved Compose configuration against actual startup.
    for key in LIMITS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(KEY, environment[KEY])
    from codehound.main import app

    with pytest.raises(RuntimeError, match="valid Fernet key"):
        with TestClient(app):
            pass


@pytest.mark.parametrize("source", ["env-file", "shell"])
def test_compose_forwards_configured_session_key(compose_config, monkeypatch, source):
    settings = {KEY: SYNTHETIC_KEY}
    environment = compose_config(
        settings if source == "env-file" else {}, settings if source == "shell" else {}
    )
    assert environment[KEY] == SYNTHETIC_KEY
    for key in LIMITS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(KEY, environment[KEY])
    from codehound.main import app

    with TestClient(app):
        assert isinstance(app.state.auth_store, SharedAuthStore)
