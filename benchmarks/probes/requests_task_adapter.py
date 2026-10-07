"""Issue-derived Requests public API observations, with no correctness assertions.

Only Requests 1921 is post-hoc: that candidate was inspected in an earlier session.
Requests 1724 and 1766 have no executable expectations in this adapter.
"""

import base64
import json
import sys
from pathlib import Path

URL = "http://example.invalid/"


def emit(token, response):
    raw = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(raw).decode(),
        flush=True,
    )


def attempt(function):
    try:
        return function()
    except Exception as exc:  # noqa: BLE001 - arbitrary candidate exceptions are observations
        return {"exception": type(exc).__module__ + "." + type(exc).__qualname__}


def capture_request(requests, method, **kwargs):
    """Exercise public Session.request; intercept only its final transport boundary."""
    session = requests.Session()
    session.trust_env = False
    captured = []

    def capture(prepared, **send_kwargs):
        captured.append(prepared)
        response = requests.Response()
        response.status_code = 200
        response.request = prepared
        response.url = prepared.url
        response._content = b""
        response._content_consumed = True
        return response

    session.send = capture
    session.request(method, URL, **kwargs)
    if len(captured) != 1:
        raise RuntimeError(
            "Public request did not reach the capture transport exactly once."
        )
    return captured[0]


def content_length(requests, method, **kwargs):
    prepared = capture_request(requests, method, **kwargs)
    return {
        "method": prepared.method,
        "content_length": prepared.headers.get("Content-Length"),
    }


def method(requests, value):
    prepared = capture_request(requests, value)
    return {
        "method": prepared.method,
        "method_is_text": isinstance(prepared.method, str),
    }


def payload(requests, value):
    prepared = capture_request(requests, "PUT", data=value)
    body = prepared.body
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not isinstance(body, bytes):
        raise TypeError("Prepared body is neither text nor bytes.")
    return {
        "body_hex": body.hex(),
        "content_length": prepared.headers.get("Content-Length"),
    }


def header_probe(requests, scenario):
    session = requests.Session()
    session.trust_env = False
    accept_before = session.headers.get("Accept")
    request_headers = {}
    if scenario == "session_none":
        session.headers["Accept-Encoding"] = None
    elif scenario == "request_none":
        request_headers["Accept-Encoding"] = None
    elif scenario == "delete_header":
        del session.headers["Accept-Encoding"]
    elif scenario == "override":
        request_headers["Accept-Encoding"] = "identity"
    prepared = session.prepare_request(
        requests.Request("GET", URL, headers=request_headers)
    )
    observed = {
        "accept_encoding_present": "Accept-Encoding" in prepared.headers,
        "accept_present": "Accept" in prepared.headers,
        "accept_unchanged": prepared.headers.get("Accept") == accept_before,
    }
    if scenario == "override":
        observed["accept_encoding_value"] = prepared.headers.get("Accept-Encoding")
    return observed


def observe(requests, task_id):
    if task_id == "psf__requests-1142":
        return {
            "bodyless_get": attempt(lambda: content_length(requests, "GET")),
            "get_explicit_zero_length": attempt(
                lambda: content_length(
                    requests,
                    "GET",
                    headers={"Content-Length": "0"},
                )
            ),
            "get_with_payload": attempt(
                lambda: content_length(requests, "GET", data="abc")
            ),
            "empty_post": attempt(lambda: content_length(requests, "POST")),
            "post_with_payload": attempt(
                lambda: content_length(requests, "POST", data="abc")
            ),
        }
    if task_id == "psf__requests-2317":
        return {
            "bytes_get": attempt(lambda: method(requests, b"GET")),
            "lowercase_bytes_post": attempt(lambda: method(requests, b"post")),
            "text_get": attempt(lambda: method(requests, "GET")),
            "lowercase_text_patch": attempt(lambda: method(requests, "patch")),
        }
    if task_id == "psf__requests-2931":
        return {
            "issue_utf8_binary": attempt(lambda: payload(requests, "ööö".encode())),
            "non_utf8_binary": attempt(lambda: payload(requests, b"\xff\x00\x80abc")),
            "ascii_binary": attempt(lambda: payload(requests, b"hello")),
            "form_regression": attempt(lambda: payload(requests, {"a": "b"})),
        }
    if task_id == "psf__requests-1921":
        return {
            name: attempt(lambda name=name: header_probe(requests, name))
            for name in (
                "session_none",
                "request_none",
                "delete_header",
                "override",
            )
        }
    raise ValueError("This task is explicitly unsupported by the frozen adapter.")


def main():
    token, task_id = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path.insert(0, "/workspace")
    try:
        import requests

        if not Path(requests.__file__).resolve().is_relative_to(Path("/workspace")):
            raise RuntimeError("Requests did not load from the pinned workspace.")
    except Exception:  # noqa: BLE001 - import/setup failures are infrastructure abstentions
        emit(
            token,
            {
                "kind": "adapter_error",
                "message": "Pinned Requests target could not load.",
            },
        )
        return
    try:
        emit(token, {"kind": "returned", "value": observe(requests, task_id)})
    except Exception as exc:  # noqa: BLE001 - retain candidate errors as raw evidence
        emit(
            token,
            {
                "kind": "raised",
                "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            },
        )


if __name__ == "__main__":
    main()
