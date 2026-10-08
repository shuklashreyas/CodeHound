"""POST-HOC public Requests preparation observations; no expected values/assertions."""

import base64
import json
import sys
from pathlib import Path


def emit(token, response):
    raw = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(raw).decode(),
        flush=True,
    )


def main():
    token = sys.argv[1]
    sys.dont_write_bytecode = True
    sys.path.insert(0, "/workspace")
    try:
        import requests

        source = Path(requests.__file__).resolve()
        if not source.is_relative_to(Path("/workspace")):
            raise RuntimeError("Requests was not imported from the retained workspace.")
        observations = {}
        cases = (
            ("session_none", {"X-Test": None, "X-Preserved": "session"}, {}),
            (
                "request_none",
                {"X-Test": "session", "X-Preserved": "session"},
                {"X-Test": None},
            ),
            (
                "override",
                {"X-Test": "session", "X-Preserved": "session"},
                {"X-Test": "request"},
            ),
        )
        for name, session_headers, request_headers in cases:
            session = requests.Session()
            session.headers = session_headers
            prepared = session.prepare_request(
                requests.Request(
                    "GET",
                    "http://example.invalid/",
                    headers=request_headers,
                )
            )
            observations[name] = {
                str(key).lower(): value
                for key, value in prepared.headers.items()
                if str(key).lower().startswith("x-")
            }
        session = requests.Session()
        accept_before = session.headers.get("Accept")
        session.headers["Accept-Encoding"] = None
        prepared = session.prepare_request(
            requests.Request("GET", "http://example.invalid/")
        )
        observations["issue_example_default_session"] = {
            "accept_encoding_present": "Accept-Encoding" in prepared.headers,
            "accept_present": "Accept" in prepared.headers,
            "accept_unchanged": prepared.headers.get("Accept") == accept_before,
        }
        emit(token, {"kind": "returned", "value": observations})
    except Exception as exc:  # noqa: BLE001 - retain arbitrary candidate exception as data
        emit(
            token,
            {
                "kind": "raised",
                "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            },
        )


if __name__ == "__main__":
    main()
