"""Controller identity checks use isolated trusted package copies, never upstream code."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import test_jobs

from codehound.execution import worker
from codehound.execution.provenance import (
    MAX_SOURCE_BYTES,
    EvaluatorChanged,
    controller_binding,
    read_trusted_source,
)
from codehound.main import app

ready = test_jobs.ready
PACKAGE = Path(__file__).parents[1] / "src" / "codehound"
PRELUDE = """
import asyncio, importlib, json, os, sys
from dataclasses import replace
from pathlib import Path
from codehound.execution import independent
from codehound.execution.docker import ExecutionResult
from codehound.execution.profiles import TrustedSuite
from codehound.execution.provenance import EvaluatorChanged
profile = TrustedSuite.model_validate({
    'name': 'identity-test', 'module': 'example', 'function': 'answer',
    'cases': [{'id': 'one', 'expect': {'kind': 'value', 'value': 1}}]
})
observation = replace(
    ExecutionResult('completed', 0, '', '', 0, 'sha256:'+'a'*64, False, False, 30),
    call_response={'kind': 'returned', 'value': 1}
)
calls = []
async def observe(*args):
    calls.append(True)
    return observation
independent.IndependentRunner.observe = observe
runner = independent.IndependentRunner('sha256:'+'a'*64)
"""


@pytest.fixture
def isolated_package(tmp_path):
    shutil.copytree(PACKAGE, tmp_path / "codehound", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def isolated_run(directory, program, *, optimized=False):
    result = subprocess.run(
        [sys.executable, *(["-O"] if optimized else []), "-c", program],
        cwd=directory,
        env=dict(os.environ, PYTHONPATH=str(directory)),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("target", ["independent.py", "protocol.py", "provenance.py"])
def test_loaded_controller_cannot_claim_new_source_bytes(isolated_package, target):
    program = (
        PRELUDE
        + f"""
path = Path(independent.__file__).parent / {target!r}
source = path.read_text()
if {target!r} == 'independent.py':
    source = source.replace(
        'passed = response["kind"] == "returned" and '
        'json_equal(response["value"], case.expect.value)',
        'passed = False  # isolated deployment edit'
    )
else:
    source += '\\n# isolated deployment edit\\n'
path.write_text(source)
try:
    asyncio.run(runner.run(Path('.'), profile))
except EvaluatorChanged as error:
    print(json.dumps({{'rejected': True, 'calls': len(calls), 'message': str(error)}}))
else:
    raise AssertionError('Stale controller published newly labeled evidence')
"""
    )
    result = isolated_run(isolated_package, program)
    assert result["rejected"] and result["calls"] == 0
    assert str(isolated_package) not in result["message"]


@pytest.mark.parametrize("change", ["edit", "remove"])
def test_mid_run_source_change_or_removal_cannot_publish_a_result(isolated_package, change):
    program = (
        PRELUDE
        + f"""
path = Path(independent.__file__)
async def changed(*args):
    if {change!r} == 'edit':
        path.write_text(path.read_text() + '\\n# mid-run deployment edit\\n')
    else:
        path.unlink()
    return observation
independent.IndependentRunner.observe = changed
try:
    asyncio.run(runner.run(Path('.'), profile))
except EvaluatorChanged:
    print(json.dumps({{'rejected': True}}))
else:
    raise AssertionError('Changed controller published evidence')
"""
    )
    assert isolated_run(isolated_package, program)["rejected"]


def test_stale_timestamp_pyc_is_rejected_at_import(isolated_package):
    source = isolated_package / "codehound" / "execution" / "identity_fixture.py"
    source.write_text(
        "from codehound.execution.provenance import bind_source\n"
        "BINDING = bind_source(__file__)\n"
        "def value(): return 'old'\n"
    )
    program = """
import importlib, json, os, sys
from pathlib import Path
from codehound.execution import identity_fixture
from codehound.execution.provenance import EvaluatorChanged
path = Path(identity_fixture.__file__)
info = path.stat()
path.write_text(path.read_text().replace("'old'", "'new'"))
os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
del sys.modules['codehound.execution.identity_fixture']
try:
    importlib.import_module('codehound.execution.identity_fixture')
except EvaluatorChanged:
    print(json.dumps({'stale_pyc_rejected': True}))
else:
    raise AssertionError('Stale pyc was bound to new source')
"""
    assert isolated_run(isolated_package, program)["stale_pyc_rejected"]


@pytest.mark.parametrize("optimized", [False, True])
def test_fixed_evaluator_identity_survives_unrelated_lazy_imports(isolated_package, optimized):
    program = (
        PRELUDE
        + """
before = asyncio.run(runner.run(Path('.'), profile))
# Worker imports optional analyzers and requirements absent from the first run.
from codehound.execution import worker
after = asyncio.run(runner.run(Path('.'), profile))
print(json.dumps({
    'same_evaluator': before.evaluator_sha256 == after.evaluator_sha256,
    'same_controller': before.controller_binding == after.controller_binding,
    'outcomes': [
        before.test_report['tests'][0]['outcome'], after.test_report['tests'][0]['outcome']
    ],
    'controller': after.controller_binding,
}))
"""
    )
    result = isolated_run(isolated_package, program, optimized=optimized)
    assert result["same_evaluator"] and result["same_controller"]
    assert result["outcomes"] == ["passed", "passed"]
    identity = result["controller"]
    assert identity["sources"]["execution/provenance.py"]
    assert identity["python"]["implementation"] == sys.implementation.name
    assert identity["python"]["version"] and identity["python"]["cache_tag"]
    assert "execution/worker.py" not in identity["sources"]
    assert all(not label.startswith("/") for label in identity["sources"])


def test_main_module_cli_import_binding_remains_usable(isolated_package):
    result = subprocess.run(
        [sys.executable, "-m", "codehound.execution.verify", "--help"],
        cwd=isolated_package,
        env=dict(os.environ, PYTHONPATH=str(isolated_package)),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "--image-id" in result.stdout


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "fifo", "oversized"])
def test_trusted_source_reads_reject_unbounded_or_nonregular_inputs(tmp_path, kind, monkeypatch):
    path = tmp_path / "source.py"
    if kind == "symlink":
        real = tmp_path / "real.py"
        real.write_text("pass")
        path.symlink_to(real)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
        original_open = os.open

        def bounded_open(opened, flags, *args, **kwargs):
            if opened == path:
                assert flags & os.O_NONBLOCK, "FIFO read must be nonblocking"
            return original_open(opened, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", bounded_open)
    elif kind == "oversized":
        path.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    with pytest.raises(EvaluatorChanged) as error:
        read_trusted_source(path)
    assert str(path) not in str(error.value)


def test_worker_source_change_failure_is_distinct_and_discards_artifact(ready):
    _, identifier, store, profile = ready
    store.enqueue(identifier, 123, profile, "sha256:" + "a" * 64)
    job = store.claim()

    async def failed(*_):
        raise EvaluatorChanged()

    asyncio.run(worker.process_job(job, app.state.database, asyncio.Event(), execute=failed))
    final = store.get(job.id, 123)
    assert final.status == "failed" and final.failure["code"] == "evaluator_changed"
    assert final.artifact is None and final.assessment is None


def test_aggregate_digest_binds_runtime_and_relative_source_inventory():
    import hashlib

    identity = controller_binding(("execution/worker.py",)).to_dict()
    digest = identity.pop("sha256")
    assert (
        hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == digest
    )
    assert set(identity["sources"]) == {"execution/worker.py", "execution/provenance.py"}
