import base64
import json
from dataclasses import replace

from test_results import execution

from codehound.benchmark.requests_1921_diagnostic import EXPECTED, compare_observation


def framed(value, token="token"):
    raw = json.dumps({"kind": "returned", "value": value}).encode()
    return "CODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(raw).decode()


def test_host_comparison_requires_complete_expected_public_headers():
    run = replace(execution({"case": "passed"}), stdout=framed(EXPECTED))
    assert compare_observation(run, "token")["matches_expected"] is True
    changed = dict(EXPECTED) | {"request_none": {"x-test": "session", "x-preserved": "session"}}
    run = replace(run, stdout=framed(changed))
    assert compare_observation(run, "token")["matches_expected"] is False
    assert compare_observation(run, "token")["scenario_matches"]["request_none"] is False


def test_timeout_missing_or_duplicate_frame_abstains_instead_of_matching():
    run = replace(execution({"case": "passed"}), stdout=framed(EXPECTED))
    for changed in (
        replace(run, status="timeout"),
        replace(run, output_truncated=True),
        replace(run, stdout=""),
        replace(run, stdout=run.stdout + "\n" + run.stdout),
    ):
        outcome = compare_observation(changed, "token")
        assert outcome["matches_expected"] is None
        assert outcome["comparison_status"] == "unavailable"


def test_exact_issue_example_requires_header_absence_and_preserved_default_accept():
    baseline = dict(EXPECTED) | {
        "issue_example_default_session": {
            "accept_encoding_present": True,
            "accept_present": True,
            "accept_unchanged": True,
        },
    }
    run = replace(execution({"case": "passed"}), stdout=framed(baseline))
    result = compare_observation(run, "token")
    assert result["matches_expected"] is False
    assert result["scenario_matches"]["issue_example_default_session"] is False
    missing_accept = dict(EXPECTED) | {
        "issue_example_default_session": {
            "accept_encoding_present": False,
            "accept_present": False,
            "accept_unchanged": False,
        },
    }
    run = replace(run, stdout=framed(missing_accept))
    assert compare_observation(run, "token")["matches_expected"] is False
