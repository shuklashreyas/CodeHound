"""Container-only entrypoint for lower-trust frozen repository pytest evidence."""

import base64
import json
import os
import sys

import pytest

# This module is loaded before any repository import. The reporter remains an
# in-process observer, so repository code can still tamper with its evidence.
sys.path.insert(0, "/harness")
from pytest_runner import Reporter  # noqa: E402


def main():
    token, config = sys.argv[1], json.loads(sys.argv[2])
    reporter = Reporter()
    sys.dont_write_bytecode = True
    os.environ.pop("PYTEST_ADDOPTS", None)
    targets = set(config["target_packages"])
    # Pytest can preload packages such as packaging. Tests must import the
    # selected revision rather than reusing an installed distribution cache.
    for name in list(sys.modules):
        if name.split(".")[0] in targets:
            del sys.modules[name]
    for directory in reversed(config["source_directories"]):
        sys.path.insert(0, "/workspace" if directory == "." else f"/workspace/{directory}")
    sys.path.insert(0, "/repository-tests")
    code = pytest.main(
        [
            "-q",
            "-s",
            "-c",
            "/harness/pytest.ini",
            "--rootdir=/repository-tests",
            "--confcutdir=/repository-tests",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            *[f"/repository-tests/{path}" for path in config["test_paths"]],
        ],
        plugins=[reporter],
    )
    payload = json.dumps(reporter.evidence(code), allow_nan=False, separators=(",", ":"))
    encoded = base64.b64encode(payload.encode()).decode()
    imports = []
    for name, module in sorted(sys.modules.copy().items()):
        if name.split(".")[0] in targets:
            source = getattr(module, "__file__", None)
            paths = [source] if source else list(getattr(module, "__path__", []))
            imports.append({"module": name, "paths": [os.path.realpath(path) for path in paths]})
    print(
        f"\nCODEHOUND_REPOSITORY_IMPORTS_V1:{token}:"
        + json.dumps(imports, allow_nan=False, separators=(",", ":")),
        flush=True,
    )
    print(f"\nCODEHOUND_TEST_REPORT_V1:{token}:{encoded}", flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
