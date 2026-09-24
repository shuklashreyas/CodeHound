"""Untrusted process output must fail closed without breaking the worker."""

import base64
import json

import pytest

from codehound.execution.independent import decode_response
from codehound.execution.protocol import load_evidence
from codehound.execution.results import decode_report


def frame(prefix, raw):
    return prefix + "nonce:" + base64.b64encode(raw.encode()).decode()


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind":"returned","value":1,"value":2}',
        '{"kind":"raised","exception":"builtins.ValueError","extra":1}',
        '{"kind":"raised","exception":"builtins.ValueError","extra":NaN}',
        '{"kind":"returned","value":NaN}',
        '{"kind":"returned","value":Infinity}',
        '{"kind":"returned","value":1e999}',
        '{"kind":"returned","value":{"x":1,"x":2}}',
        '{"kind":"adapter_error","message":[]}',
        '{"kind":"adapter_error"}',
        '{"kind":[],"value":1}',
        '{"kind":"returned","value":' + "[" * 80 + "0" + "]" * 80 + "}",
        "[" * 2000 + "0" + "]" * 2000,
        "[]",
        "null",
        r'{"kind":"returned","value":"\ud800"}',
        r'{"kind":"returned","value":{"\udfff":1}}',
    ],
)
def test_candidate_response_rejects_ambiguous_or_malformed_json(raw):
    assert decode_response(frame("CODEHOUND_CALL_V1:", raw), "nonce") == (
        None,
        "invalid_response",
    )


@pytest.mark.parametrize(
    "response",
    [
        {"kind": "returned", "value": {"a": [1, True, None, "text", 2.5]}},
        {"kind": "raised", "exception": "builtins.ValueError"},
        {"kind": "adapter_error", "message": "Missing candidate target."},
    ],
)
def test_valid_candidate_envelopes_remain_supported(response):
    assert decode_response(frame("CODEHOUND_CALL_V1:", json.dumps(response)), "nonce") == (
        response,
        None,
    )


def test_deep_pytest_report_cannot_raise_recursion_error():
    raw = "[" * 2000 + "0" + "]" * 2000
    assert decode_report(frame("CODEHOUND_TEST_REPORT_V1:", raw), "nonce", 0) == (
        None,
        "invalid_report",
    )


@pytest.mark.parametrize(
    "change", [{"schema_version": True}, {"untrusted": 1}, {"duration_seconds": 1}]
)
def test_pytest_schema_has_no_ambiguous_version_or_unknown_fields(change):
    report = {
        "schema_version": 1,
        "exit_code": 0,
        "collected": [],
        "tests": [],
        "collection_errors": [],
    } | change
    assert (
        decode_report(frame("CODEHOUND_TEST_REPORT_V1:", json.dumps(report)), "nonce", 0)[1]
        == "invalid_report"
    )


def test_evidence_byte_limit_and_duplicate_keys():
    with pytest.raises(ValueError):
        load_evidence(b'"too much"', limit=2)
    with pytest.raises(ValueError):
        load_evidence(b'{"x":1,"x":2}', limit=100)
