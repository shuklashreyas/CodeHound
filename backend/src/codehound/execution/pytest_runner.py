"""Trusted entrypoint mounted read-only into the test container."""

import sys

import pytest

# Import the trusted pytest installation before exposing candidate modules.
sys.dont_write_bytecode = True
sys.path.insert(0, "/workspace")
sys.path.insert(0, "/workspace/src")
raise SystemExit(
    pytest.main(
        [
            "-q",
            "-s",
            "-c",
            "/harness/pytest.ini",
            "--confcutdir=/tests",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            "/tests",
        ]
    )
)
