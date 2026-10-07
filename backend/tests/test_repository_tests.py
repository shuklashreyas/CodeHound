import asyncio
import os
from dataclasses import replace

import pytest
from pydantic import ValidationError

from codehound.evaluation.registry import EvaluationProfile, load_profiles
from codehound.execution.repository_tests import (
    SOURCE,
    RepositoryTestConfig,
    RepositoryTestRunner,
    decode_imports,
    freeze_repository_tests,
    run_repository_tests,
)
from codehound.execution.results import compare_tests
from codehound.execution.verify import verify_snapshot
from codehound.repositories.checkout import Checkouts
from codehound.repositories.intake import collect_snapshot
from codehound.repositories.urls import parse_pull_url

IMAGE = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "paths",
    [
        ["/etc"],
        ["."],
        [".."],
        ["tests/../src"],
        ["tests//unit"],
        ["tests/"],
        ["tests;echo"],
        ["-p"],
        [".git"],
        ["tests/.env"],
        ["tests\\unit"],
        ["tests", "tests/unit"],
        ["tests", "tests"],
    ],
)
def test_config_rejects_unsafe_or_overlapping_paths(paths):
    with pytest.raises(ValidationError):
        RepositoryTestConfig(test_paths=paths)


def test_config_is_operator_only_and_bounded():
    for values in (
        {"test_paths": []},
        {"test_paths": [f"tests/{index}" for index in range(21)]},
        {"test_paths": ["tests"], "source_directories": ["../outside"]},
        {"test_paths": ["tests"], "timeout_seconds": 121},
        {"test_paths": ["tests"], "command": "pip install ."},
        {"test_paths": ["tests"], "target_packages": ["packaging.utils"]},
        {"test_paths": ["tests"], "target_packages": ["pkg", "pkg"]},
    ):
        with pytest.raises(ValidationError):
            RepositoryTestConfig.model_validate(values)
    profile = load_profiles()["codehound-url-contract"].model_dump()
    profile["repository_tests"] = {"test_paths": ["tests"]}
    parsed = EvaluationProfile.model_validate(profile)
    assert parsed.repository_tests.test_paths == ["tests"]
    assert "test_paths" not in str(parsed.public())
    assert parsed.public()["repository_tests"]["selected_paths"] == 1
    assert parsed.public()["repository_tests"]["trust"] == "repository_controlled"


def test_freeze_uses_baseline_files_and_hashes_without_executing_python(tmp_path):
    baseline = tmp_path / "baseline"
    tests = baseline / "tests" / "unit"
    tests.mkdir(parents=True)
    (tests / "test_value.py").write_text("raise RuntimeError('never execute on host')\n")
    (baseline / "conftest.py").write_text("raise RuntimeError('also never execute on host')\n")
    (tests.parent / "conftest.py").write_text("BASELINE = True\n")
    (tests.parent / "__init__.py").write_text("")
    (tests / "data.json").write_text('{"value": 2}')
    (tests / ".env").write_text("EXCLUDE_ME=secret")
    config = RepositoryTestConfig(test_paths=["tests/unit"])
    with freeze_repository_tests(baseline, config, "a" * 40) as frozen:
        directory = frozen.directory
        original = (tests / "test_value.py").read_bytes()
        (tests / "test_value.py").write_text("different baseline after freeze\n")
        assert (directory / "tests/unit/test_value.py").read_bytes() == original
        assert (directory / "tests/conftest.py").is_file()
        assert (directory / "conftest.py").is_file()
        assert not (directory / "tests/unit/.env").exists()
        assert frozen.provenance["baseline_sha"] == "a" * 40
        assert frozen.provenance["configuration_sha256"] == config.sha256
        assert len(frozen.provenance["test_suite_sha256"]) == 64
        assert frozen.provenance["file_count"] == 5
        assert all(len(item["sha256"]) == 64 for item in frozen.provenance["files"])
    assert not directory.exists()


@pytest.mark.parametrize("location", ["selected", "ancestor", "nested"])
def test_freeze_rejects_symlinks_even_inside_selected_directories(tmp_path, location):
    root, outside = tmp_path / "baseline", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "test_value.py").write_text("assert True")
    tests = root / "tests"
    if location == "selected":
        tests.symlink_to(outside, target_is_directory=True)
    else:
        tests.mkdir()
        (tests / "test_value.py").write_text("assert True")
        (root / "conftest.py" if location == "ancestor" else tests / "linked").symlink_to(
            outside, target_is_directory=True
        )
    with pytest.raises(ValueError, match="symlinks"):
        with freeze_repository_tests(root, RepositoryTestConfig(test_paths=["tests"]), "a" * 40):
            pass


def test_size_bound_and_missing_paths_are_inconclusive_optional_evidence(tmp_path, monkeypatch):
    from codehound.execution import repository_tests

    root = tmp_path / "baseline"
    root.mkdir()
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_value.py").write_text("x" * 20)
    monkeypatch.setattr(repository_tests, "MAX_BYTES", 10)
    checkouts = Checkouts(root, root, "a" * 40, "b" * 40)
    for path in ("tests", "missing"):
        artifact = asyncio.run(
            run_repository_tests(checkouts, RepositoryTestConfig(test_paths=[path]), IMAGE)
        )
        assert artifact["status"] == "unavailable"
        assert artifact["comparison"] == "inconclusive"
        assert artifact["affects_assessment"] is False
        assert artifact["error_code"] == "repository_test_inputs_unavailable"


def test_freeze_rejects_non_regular_files_and_file_count_limit(tmp_path, monkeypatch):
    from codehound.execution import repository_tests

    root = tmp_path / "baseline"
    tests = root / "tests"
    tests.mkdir(parents=True)
    fifo = tests / "fixture.pipe"
    os.mkfifo(fifo)
    config = RepositoryTestConfig(test_paths=["tests"])
    with pytest.raises(ValueError, match="regular files"):
        with freeze_repository_tests(root, config, "a" * 40):
            pass
    fifo.unlink()
    for name in ("test_a.py", "test_b.py"):
        (tests / name).write_text("assert True")
    monkeypatch.setattr(repository_tests, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="file limit"):
        with freeze_repository_tests(root, config, "a" * 40):
            pass


def test_cancelled_repository_execution_removes_frozen_inputs(tmp_path):
    root = tmp_path / "baseline"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_a.py").write_text("def test_a(): pass")
    paths = []
    started = asyncio.Event()

    class Runner:
        async def run(self, workspace, frozen):
            paths.append(frozen.directory)
            started.set()
            await asyncio.sleep(60)

    async def run():
        task = asyncio.create_task(
            run_repository_tests(
                Checkouts(root, root, "a" * 40, "b" * 40),
                RepositoryTestConfig(test_paths=["tests"]),
                IMAGE,
                runner=Runner(),
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert paths and not paths[0].exists()


def test_optional_repository_improvement_cannot_override_independent_regression(
    github_bundle, tmp_path
):
    from test_verify import run_result

    snapshot = asyncio.run(
        collect_snapshot(parse_pull_url("https://github.com/octocat/project/pull/7"), github_bundle)
    )
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for directory in (baseline, candidate):
        (directory / "tests").mkdir(parents=True)
    (baseline / "tests/test_original.py").write_text("def test_original(): assert False\n")
    (candidate / "tests/test_replacement.py").write_text("def test_original(): pass\n")
    frozen_seen = []

    class Workspace:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return Checkouts(baseline, candidate, snapshot["base_sha"], snapshot["head_sha"])

        async def __aexit__(self, *_):
            pass

    class Independent:
        image_id = IMAGE

        async def run(self, workspace, suite):
            return replace(
                run_result(0 if workspace == baseline else 1),
                evidence_source="external_json_assertions",
            )

    class Repository:
        async def run(self, workspace, frozen):
            frozen_seen.append(frozen.directory)
            assert (frozen.directory / "tests/test_original.py").is_file()
            assert not (frozen.directory / "tests/test_replacement.py").exists()
            return replace(run_result(1 if workspace == baseline else 0), evidence_source=SOURCE)

    artifact = asyncio.run(
        verify_snapshot(
            snapshot,
            {"visible": load_profiles()["codehound-url-contract"].visible},
            Independent(),
            mode="independent",
            workspace_factory=Workspace,
            repository_test_config={"test_paths": ["tests"]},
            repository_test_runner=Repository(),
        )
    )
    assert artifact["assessment"]["verdict"] == "regression_detected"
    assert artifact["repository_tests"]["comparison"] == "candidate_improves"
    assert artifact["repository_tests"]["trust"] == "repository_controlled"
    assert frozen_seen[0] == frozen_seen[1] and not frozen_seen[0].exists()


@pytest.fixture
def image_id():
    image = os.getenv("CODEHOUND_TEST_IMAGE_ID")
    if not image:
        pytest.skip("Trusted Docker test image not configured")
    return image


def test_actual_frozen_baseline_fixture_and_candidate_conftest_isolation(image_id, tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for directory, value in ((baseline, 1), (candidate, 2)):
        directory.mkdir()
        (directory / "src").mkdir()
        (directory / "src/feature.py").write_text(f"VALUE = {value}\n")
        (directory / "tests").mkdir()
    (baseline / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef expected(): return 2\n"
    )
    (baseline / "tests/test_feature.py").write_text(
        "from feature import VALUE\ndef test_feature(expected): assert VALUE == expected\n"
    )
    (candidate / "conftest.py").write_text("raise RuntimeError('candidate conftest loaded')\n")
    (candidate / "pytest.ini").write_text("[pytest]\naddopts = --ignore=/repository-tests\n")
    (candidate / "tests/test_feature.py").write_text("def test_false_pass(): pass\n")
    (candidate / "tests/test_added.py").write_text("raise RuntimeError('candidate test loaded')\n")
    checkouts = Checkouts(baseline, candidate, "a" * 40, "b" * 40)
    artifact = asyncio.run(
        run_repository_tests(
            checkouts,
            RepositoryTestConfig(test_paths=["tests"], target_packages=["feature"]),
            image_id,
        )
    )
    assert artifact["comparison"] == "candidate_improves", artifact
    assert artifact["baseline"]["exit_code"] == 1
    assert artifact["candidate"]["exit_code"] == 0
    assert artifact["candidate"]["test_report"]["collected"] == [
        "tests/test_feature.py::test_feature"
    ]
    assert artifact["candidate"]["evidence_source"] == SOURCE
    assert artifact["candidate"]["target_imports"] == [
        {"module": "feature", "paths": ["/workspace/src/feature.py"]}
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [{"module": "packaging", "paths": ["/usr/local/lib/python3.12/site-packages/packaging"]}],
        [{"module": "packaging", "paths": ["/workspace/../harness/packaging.py"]}],
        [{"module": "packaging", "paths": []}],
        [{"module": "unrelated", "paths": ["/workspace/src/unrelated.py"]}],
    ],
)
def test_import_provenance_rejects_wrong_or_missing_target(entries):
    import json

    from codehound.execution.repository_tests import IMPORT_PREFIX

    raw = IMPORT_PREFIX + "test:" + json.dumps(entries)
    imports, error = decode_imports(raw, "test", ["packaging"])
    assert imports is None and error == "target_import_provenance_invalid"


def test_actual_preloaded_installed_package_is_replaced_by_revision(image_id, tmp_path):
    root = tmp_path / "workspace"
    (root / "src/packaging").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/packaging/__init__.py").write_text("PINNED_REVISION = 'candidate'\n")
    (root / "tests/test_package.py").write_text(
        "import packaging\ndef test_revision(): assert packaging.PINNED_REVISION == 'candidate'\n"
    )
    config = RepositoryTestConfig(test_paths=["tests"], target_packages=["packaging"])
    with freeze_repository_tests(root, config, "a" * 40) as frozen:
        result = asyncio.run(RepositoryTestRunner(image_id).run(root, frozen))
    assert result.exit_code == 0 and result.evidence_error is None, result
    assert result.target_imports == [
        {"module": "packaging", "paths": ["/workspace/src/packaging/__init__.py"]}
    ]


def test_actual_installed_package_without_checkout_source_is_inconclusive(image_id, tmp_path):
    root = tmp_path / "workspace"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_package.py").write_text(
        "import packaging\ndef test_package(): assert packaging.__version__\n"
    )
    config = RepositoryTestConfig(test_paths=["tests"], target_packages=["packaging"])
    with freeze_repository_tests(root, config, "a" * 40) as frozen:
        result = asyncio.run(RepositoryTestRunner(image_id).run(root, frozen))
    assert result.exit_code == 0, result
    assert result.test_report is None
    assert result.evidence_error == "target_import_provenance_invalid"
    assert compare_tests(result, result)["verdict"] == "inconclusive"


def test_actual_repository_controlled_tampering_is_lower_trust(image_id, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "tests/test_exit.py").write_text("import os\ndef test_exit(): os._exit(0)\n")
    config = RepositoryTestConfig(test_paths=["tests"])
    with freeze_repository_tests(root, config, "a" * 40) as frozen:
        result = asyncio.run(RepositoryTestRunner(image_id).run(root, frozen))
    assert result.exit_code == 0 and result.evidence_error == "missing_report"
    assert result.evidence_source == SOURCE
    assert compare_tests(result, result)["verdict"] == "inconclusive"


def test_actual_repository_tests_sandbox_and_collection_failure(image_id, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "tests").mkdir()
    script = root / "tests/test_isolation.py"
    script.write_text("""import os
from pathlib import Path
import socket
import pytest

def test_isolation():
    assert os.getuid() == 65534
    assert not Path('/var/run/docker.sock').exists()
    with pytest.raises(OSError): Path('/workspace/changed').write_text('bad')
    with pytest.raises(OSError): Path('/repository-tests/changed').write_text('bad')
    with pytest.raises(OSError): socket.create_connection(('1.1.1.1',443),timeout=0.2)
""")
    config = RepositoryTestConfig(test_paths=["tests"])
    with freeze_repository_tests(root, config, "a" * 40) as frozen:
        result = asyncio.run(RepositoryTestRunner(image_id).run(root, frozen))
    assert result.exit_code == 0 and result.evidence_error is None, result
    script.write_text("raise RuntimeError('collection broke')\n")
    artifact = asyncio.run(
        run_repository_tests(Checkouts(root, root, "a" * 40, "b" * 40), config, image_id)
    )
    assert artifact["comparison"] == "inconclusive", artifact
    assert artifact["candidate"]["test_report"]["collection_errors"]
