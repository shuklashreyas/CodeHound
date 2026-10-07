"""Issue/baseline-derived public pytest observations; expectations stay on the host."""

import base64
import json
import os
import sys
import traceback
import types
from pathlib import Path
from tempfile import TemporaryDirectory

SOURCES = {
    "pytest-dev__pytest-10051": """import json, logging, os
from pathlib import Path

def test_caplog_clear(caplog):
    logging.warning("before")
    data = {"before_consistent": caplog.get_records("call") == caplog.records}
    caplog.clear()
    data["after_clear_empty"] = caplog.get_records("call") == []
    logging.warning("after")
    data["after_consistent"] = caplog.get_records("call") == caplog.records
    data["after_messages"] = [r.getMessage() for r in caplog.get_records("call")]
    Path(os.environ["PROBE_OBSERVATIONS"]).write_text(json.dumps(data))
""",
    "pytest-dev__pytest-10356": """import pytest
@pytest.mark.foo
class Foo:
    pass
@pytest.mark.bar
class Bar:
    pass
class TestBoth(Foo, Bar):
    def test_marks(self): pass
class TestReverse(Bar, Foo):
    def test_marks(self): pass
class TestSingle(Foo):
    def test_marks(self): pass
@pytest.mark.own
class TestOwn(Foo, Bar):
    def test_marks(self): pass
""",
    "pytest-dev__pytest-5262": """import json, os, sys
from pathlib import Path

def test_captured_text_mode(capfd):
    text = "probe-snowman-\\u2603"
    binary_mode = "b" in getattr(sys.stdout, "mode", "")
    data = {"mode_is_text": not binary_mode}
    try:
        sys.stdout.write(text.encode("utf-8") if binary_mode else text)
        data["mode_selected_unicode_write"] = True
    except (TypeError, ValueError):
        data["mode_selected_unicode_write"] = False
    data["unicode_roundtrip"] = text in capfd.readouterr().out
    try:
        sys.stdout.write("text-control")
        data["direct_text_write"] = "text-control" in capfd.readouterr().out
    except (TypeError, ValueError):
        data["direct_text_write"] = False
    Path(os.environ["PROBE_OBSERVATIONS"]).write_text(json.dumps(data))
""",
    "pytest-dev__pytest-5631": """import json, os
from pathlib import Path
from unittest.mock import patch
import numpy as np
import helper

def retain(name, value):
    path = Path(os.environ["PROBE_OBSERVATIONS"])
    data = json.loads(path.read_text()) if path.exists() else {}
    data[name] = value
    path.write_text(json.dumps(data))

@patch("helper.value", new=np.array([-5.5, 3.0]))
def test_numpy_two_values():
    retain("numpy_two_values", helper.value.tolist())

@patch("helper.value", new=np.array([2.0]))
def test_numpy_one_value():
    retain("numpy_one_value", helper.value.tolist())

@patch("helper.value", new=42)
def test_explicit_number():
    retain("explicit_number", helper.value)

@patch("helper.value")
def test_default_mock_injected(mock):
    retain("default_mock_injected", mock is helper.value)
""",
}


def emit(token, response):
    raw = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(raw).decode(),
        flush=True,
    )


def main():
    token, task_id = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path[:0] = ["/workspace/src", "/workspace"]
    # Fresh source checkouts omit setuptools-scm's generated version module.
    # Supply metadata only, without running setup or altering the retained source tree.
    if not Path("/workspace/src/_pytest/_version.py").is_file():
        metadata = types.ModuleType("_pytest._version")
        metadata.version = "0.0.0"
        metadata.version_tuple = (0, 0, 0)
        sys.modules["_pytest._version"] = metadata
    try:
        import pytest

        if not Path(pytest.__file__).resolve().is_relative_to(Path("/workspace")):
            raise RuntimeError("pytest did not load from the pinned workspace.")
    except Exception:  # noqa: BLE001 - dependency/layout errors are abstentions
        traceback.print_exc(file=sys.stderr)
        emit(
            token,
            {
                "kind": "adapter_error",
                "message": "Pinned pytest target could not load.",
            },
        )
        return
    if task_id not in SOURCES:
        emit(
            token,
            {"kind": "adapter_error", "message": "Task has no frozen public probe."},
        )
        return
    try:
        with TemporaryDirectory(prefix="pytest-task-observation-") as temporary:
            root = Path(temporary)
            observations_path = root / "observations.json"
            os.environ["PROBE_OBSERVATIONS"] = str(observations_path)
            (root / "test_probe.py").write_text(SOURCES[task_id])
            (root / "helper.py").write_text("value = None\n")
            sys.path.insert(0, str(root))
            marks = {}

            class Observer:
                def pytest_collection_finish(self, session):
                    if task_id == "pytest-dev__pytest-10356":
                        for item in session.items:
                            marks[item.parent.name] = sorted(
                                mark.name for mark in item.iter_markers()
                            )

            # Avoid repository conftest/config and third-party plugin discovery.
            # --assert=plain avoids historical AST assertion-rewriter/Python-version coupling.
            exit_code = pytest.main(
                [
                    "-q",
                    "--assert=plain",
                    "-p",
                    "no:cacheprovider",
                    "--confcutdir",
                    str(root),
                    "--rootdir",
                    str(root),
                    "-o",
                    "addopts=",
                    str(root / "test_probe.py"),
                ],
                plugins=[Observer()],
            )
            observed = (
                json.loads(observations_path.read_text())
                if observations_path.exists()
                else {}
            )
            if task_id == "pytest-dev__pytest-10356":
                observed.update(marks)
            observed["pytest_exit"] = int(exit_code)
            emit(token, {"kind": "returned", "value": observed})
    except Exception as exc:  # noqa: BLE001 - candidate pytest errors are observations
        emit(
            token,
            {
                "kind": "raised",
                "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            },
        )


if __name__ == "__main__":
    main()
