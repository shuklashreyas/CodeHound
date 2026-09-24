import asyncio
import os
from pathlib import Path

import pytest

from codehound.execution.docker import DockerRunner

FAKE_IMAGE = "sha256:" + "a" * 64
FIXTURES = Path(__file__).parents[1] / "fixtures" / "pagination"


def test_rejects_mutable_images_and_unbounded_limits():
    with pytest.raises(ValueError):
        DockerRunner("python:latest")
    with pytest.raises(ValueError):
        DockerRunner(FAKE_IMAGE, timeout_seconds=0)
    with pytest.raises(ValueError):
        DockerRunner(FAKE_IMAGE, output_limit=10_000_000)


def test_required_isolation_contract(tmp_path):
    workspace = tmp_path / "workspace"
    tests = tmp_path / "tests"
    workspace.mkdir()
    tests.mkdir()
    args = DockerRunner(FAKE_IMAGE).create_args("test", workspace, tests)
    for required in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--user=65534:65534",
        "--security-opt=no-new-privileges",
        "--memory=512m",
        "--pids-limit=64",
        "--pull=never",
    ):
        assert required in args
    assert all("readonly" in arg for arg in args if arg.startswith("type=bind"))
    assert not any("docker.sock" in arg for arg in args)
    assert "/usr/bin/timeout" in args


def test_mount_option_injection_rejected(tmp_path):
    malicious = tmp_path / "workspace,readonly=false"
    malicious.mkdir()
    with pytest.raises(ValueError):
        DockerRunner(FAKE_IMAGE).create_args("test", malicious, tmp_path)


@pytest.fixture
def image_id():
    image = os.getenv("CODEHOUND_TEST_IMAGE_ID")
    if not image:
        pytest.skip("Trusted Docker test image not configured")
    return image


def test_correct_and_overfit_fixture(image_id):
    async def run():
        runner = DockerRunner(image_id)
        for variant, expected_hidden in (("correct", 0), ("overfit", 1)):
            visible = await runner.run(FIXTURES / variant, FIXTURES / "visible")
            hidden = await runner.run(FIXTURES / variant, FIXTURES / "hidden")
            assert visible.status == "completed", visible
            assert visible.exit_code == 0, visible
            assert hidden.status == "completed", hidden
            assert hidden.exit_code == expected_hidden, hidden

    asyncio.run(run())


def test_sandbox_properties_and_trusted_test_configuration(image_id, tmp_path):
    workspace = tmp_path / "workspace"
    tests = tmp_path / "tests"
    workspace.mkdir()
    tests.mkdir()
    (workspace / "pytest.ini").write_text("[pytest]\naddopts = --ignore=/tests\n")
    (workspace / "conftest.py").write_text(
        "raise RuntimeError('Candidate conftest must not execute')\n"
    )
    (workspace / "pytest.py").write_text(
        "raise RuntimeError('Candidate pytest must not execute')\n"
    )
    (tests / "test_isolation.py").write_text("""import os
from pathlib import Path
import socket
import pytest


def test_isolation():
    assert os.getuid() == 65534
    assert "GITHUB_CLIENT_SECRET" not in os.environ
    assert not Path("/var/run/docker.sock").exists()
    with pytest.raises(OSError):
        Path("/workspace/mutated").write_text("bad")
    with pytest.raises(OSError):
        Path("/tests/mutated").write_text("bad")
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1",443),timeout=0.2)


def test_failure_is_not_hidden_by_candidate_configuration():
    assert False, "Independent test failed as intended"
""")
    result = asyncio.run(DockerRunner(image_id).run(workspace, tests))
    assert result.status == "completed", result
    assert result.exit_code == 1, result
    assert "1 failed, 1 passed" in result.stdout, result
    assert not (workspace / "mutated").exists()


def test_timeout_and_output_limit(image_id, tmp_path):
    workspace = tmp_path / "workspace"
    tests = tmp_path / "tests"
    workspace.mkdir()
    tests.mkdir()
    script = tests / "test_limits.py"
    script.write_text("import time\ndef test_slow():\n    time.sleep(30)\n")
    result = asyncio.run(DockerRunner(image_id, timeout_seconds=1).run(workspace, tests))
    assert result.status == "timeout", result
    script.write_text(
        "def test_noisy():\n    for i in range(10000):\n        print('x'*1000,flush=True)\n"
    )
    result = asyncio.run(DockerRunner(image_id, output_limit=2048).run(workspace, tests))
    assert result.status == "output_limit", result
    assert result.output_truncated
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 2048


def test_src_layout_uses_candidate_code(image_id, tmp_path):
    workspace = tmp_path / "workspace"
    package = workspace / "src" / "hound_sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 42\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_package.py").write_text(
        "from hound_sample import VALUE\ndef test_source():\n    assert VALUE == 42\n"
    )
    result = asyncio.run(DockerRunner(image_id).run(workspace, tests))
    assert result.status == "completed" and result.exit_code == 0, result


def test_cancelled_execution_removes_its_container(image_id, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from codehound.execution import docker

    suffix = uuid4().hex
    monkeypatch.setattr(docker, "uuid4", lambda: SimpleNamespace(hex=suffix))
    workspace = tmp_path / "workspace"
    tests = tmp_path / "tests"
    workspace.mkdir()
    tests.mkdir()
    (tests / "test_wait.py").write_text("import time\ndef test_wait():\n    time.sleep(60)\n")

    async def exercise():
        task = asyncio.create_task(DockerRunner(image_id).run(workspace, tests))
        try:
            for _ in range(100):
                code, state, _ = await docker.control(
                    "inspect", "--format", "{{.State.Running}}", f"codehound-{suffix}"
                )
                if code == 0 and state.strip() == b"true":
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Test container did not start")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            code, _, _ = await docker.control("inspect", f"codehound-{suffix}")
            assert code != 0, "Cancelled execution left a container behind"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_mixed_improvement_and_regression_in_real_container(image_id):
    from codehound.execution.results import compare_tests

    async def run():
        runner = DockerRunner(image_id)
        before = await runner.run(FIXTURES / "original", FIXTURES / "hidden")
        after = await runner.run(FIXTURES / "regressive", FIXTURES / "hidden")
        assert before.evidence_error is None, before
        assert after.evidence_error is None, after
        result = compare_tests(before, after)
        assert result["verdict"] == "regression_detected", result
        assert result["counts"]["improvements"] == 3
        assert result["counts"]["regressions"] == 1
        assert result["regressions"][0]["nodeid"].endswith("[0-2]")
        assert len(before.test_report["tests"]) == 7
        assert "CODEHOUND_TEST_REPORT" not in before.stdout

    asyncio.run(run())


def test_early_zero_exit_is_not_verified(image_id, tmp_path):
    from codehound.execution.results import compare_tests

    workspace, tests = tmp_path / "workspace", tmp_path / "tests"
    workspace.mkdir()
    tests.mkdir()
    (tests / "test_early.py").write_text("import os\ndef test_exit():\n    os._exit(0)\n")
    result = asyncio.run(DockerRunner(image_id).run(workspace, tests))
    assert result.status == "completed" and result.exit_code == 0
    assert result.evidence_error == "missing_report"
    assert compare_tests(result, result)["verdict"] == "inconclusive"


def test_skip_fixture_error_and_teardown_error_are_recorded(image_id, tmp_path):
    workspace, tests = tmp_path / "workspace", tmp_path / "tests"
    workspace.mkdir()
    tests.mkdir()
    (tests / "test_phases.py").write_text("""import pytest

@pytest.fixture
def broken():
    raise RuntimeError("setup broke")

@pytest.fixture
def teardown():
    yield
    raise RuntimeError("teardown broke")

def test_setup(broken):
    pass

def test_teardown(teardown):
    pass

@pytest.mark.skip(reason="unsupported")
def test_skip():
    pass

@pytest.mark.xfail(reason="known issue")
def test_expected():
    assert False
""")
    result = asyncio.run(DockerRunner(image_id).run(workspace, tests))
    assert result.evidence_error is None, result
    outcomes = {
        case["nodeid"].split("::")[-1]: case["outcome"] for case in result.test_report["tests"]
    }
    assert outcomes == {
        "test_setup": "error",
        "test_teardown": "error",
        "test_skip": "skipped",
        "test_expected": "xfailed",
    }
