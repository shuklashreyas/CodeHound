"""Container-side Python-to-JSON adapter. Contains no expected values or assertions."""

import base64
import dataclasses
import importlib
import json
import sys
from pathlib import Path


def emit(token, response):
    payload = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print("\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(payload).decode(), flush=True)


def main():
    token = sys.argv[1]
    try:
        request = json.loads(sys.stdin.readline(32769))
        sys.dont_write_bytecode = True
        sys.path.insert(0, "/workspace")
        source_directory = (Path("/workspace") / request["source_directory"]).resolve()
        if not source_directory.is_relative_to(Path("/workspace")):
            raise RuntimeError("Source directory is outside the workspace.")
        sys.path.insert(0, str(source_directory))
        module = importlib.import_module(request["module"])
        source = getattr(module, "__file__", None)
        if not source or not Path(source).resolve().is_relative_to(Path("/workspace")):
            raise RuntimeError("Target module was not loaded from the candidate workspace.")
        target = module
        for part in request["function"].split("."):
            target = getattr(target, part)
        if not callable(target):
            raise TypeError("Target is not callable.")
    except Exception:
        emit(token, {"kind": "adapter_error", "message": "Candidate target could not be loaded."})
        return
    try:
        value = target(*request["args"], **request["kwargs"])
    except Exception as exc:
        emit(
            token,
            {"kind": "raised", "exception": type(exc).__module__ + "." + type(exc).__qualname__},
        )
        return
    try:
        if request["result_encoding"] == "dataclass":
            value = dataclasses.asdict(value)
        emit(token, {"kind": "returned", "value": value})
    except (TypeError, ValueError, OverflowError):
        emit(token, {"kind": "adapter_error", "message": "Candidate result was not a JSON value."})


if __name__ == "__main__":
    main()
