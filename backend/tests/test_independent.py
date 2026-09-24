import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from codehound.execution.independent import IndependentRunner, decode_response, json_equal
from codehound.execution.profiles import TrustedSuite
from codehound.execution.results import compare_tests

FIXTURES = Path(__file__).parents[1] / "fixtures" / "pagination"


def profile(**changes):
    return TrustedSuite.model_validate(
        {
            "name": "external",
            "module": "candidate",
            "function": "answer",
            "cases": [{"id": "one", "args": [5], "expect": {"kind": "value", "value": 10}}],
        }
        | changes
    )


@pytest.fixture
def workspace(tmp_path):
    # pytest's temporary root is mode 0700 on Linux. Mount a dedicated readable
    # child, matching real Git checkouts, without weakening the container UID.
    directory = tmp_path / "workspace"
    directory.mkdir(mode=0o755)
    return directory


@pytest.fixture
def image():
    value = os.getenv("CODEHOUND_TEST_IMAGE_ID")
    if not value:
        pytest.skip("Trusted Docker image not configured")
    return value


def test_profiles_reject_duplicate_ids_and_non_json():
    one = profile().cases[0].model_dump()
    with pytest.raises(ValidationError):
        profile(cases=[one, one])
    with pytest.raises(ValidationError):
        profile(module="../candidate")
    with pytest.raises(ValidationError):
        profile(cases=[one | {"expect": {"kind": "value", "value": float("nan")}}])
    with pytest.raises(ValidationError):
        profile(cases=[one | {"shell": "echo nope"}])


def test_json_boolean_is_not_numeric_and_objects_ignore_key_order():
    assert not json_equal({"x": True}, {"x": 1})
    assert json_equal({"x": 1, "y": [2.0]}, {"y": [2], "x": 1.0})
    assert not json_equal([1], [1, 2])


def test_missing_or_forged_frame_is_not_a_result():
    assert decode_response("10", "nonce") == (None, "missing_response")
    assert decode_response("CODEHOUND_CALL_V1:nonce:not-base64", "nonce")[1] == "invalid_response"


def test_external_fixture_comparison(image):
    async def run():
        runner = IndependentRunner(image)
        suite = TrustedSuite.load(FIXTURES / "profiles" / "hidden.json")
        before = await runner.run(FIXTURES / "original", suite)
        correct = await runner.run(FIXTURES / "correct", suite)
        regressive = await runner.run(FIXTURES / "regressive", suite)
        assert before.evidence_source == "external_json_assertions"
        assert compare_tests(before, correct)["verdict"] == "candidate_improves"
        result = compare_tests(before, regressive)
        assert result["verdict"] == "regression_detected", result
        assert result["counts"]["improvements"] == 3
        assert result["counts"]["regressions"] == 1
        assert all(case["observation"]["status"] == "completed" for case in correct.case_evidence)

    asyncio.run(run())


def test_candidate_cannot_access_expectations_or_change_host_assertions(image, workspace):
    (workspace / "candidate.py").write_text("""from pathlib import Path
import pytest

def answer(value):
    assert not Path('/tests').exists()
    assert not Path('/profiles').exists()
    for path in Path('/harness').glob('*.py'):
        assert 'host-only-expectation-' not in path.read_text()
    pytest.main = lambda *args, **kwargs: 0
    return 'wrong answer'
""")
    suite = profile(
        cases=[
            {
                "id": "private-expectation",
                "args": [5],
                "expect": {"kind": "value", "value": "host-only-expectation-8237"},
            }
        ]
    )
    result = asyncio.run(IndependentRunner(image).run(workspace, suite))
    assert result.test_report["tests"][0]["outcome"] == "failed", result
    assert result.case_evidence[0]["observation"]["call_response"] == {
        "kind": "returned",
        "value": "wrong answer",
    }
    assert "host-only-expectation-8237" not in str(result.to_dict())


def test_zero_exit_and_missing_module_cannot_pass(image, workspace):
    (workspace / "candidate.py").write_text("import os\ndef answer(value):\n    os._exit(0)\n")
    result = asyncio.run(IndependentRunner(image).run(workspace, profile()))
    assert result.test_report["tests"][0]["outcome"] == "error"
    assert result.case_evidence[0]["observation"]["evidence_error"] == "missing_response"
    missing = asyncio.run(
        IndependentRunner(image).run(workspace, profile(module="json", function="loads"))
    )
    assert missing.test_report["tests"][0]["outcome"] == "error"


def test_timeout_and_no_state_shared_between_cases(image, workspace):
    (workspace / "candidate.py").write_text("""import time
count = 0

def answer(value):
    global count
    count += 1
    if value == 0:
        time.sleep(30)
    return count
""")
    suite = profile(
        timeout_seconds=1,
        cases=[
            {"id": "first", "args": [1], "expect": {"kind": "value", "value": 1}},
            {"id": "second", "args": [1], "expect": {"kind": "value", "value": 1}},
            {"id": "timeout", "args": [0], "expect": {"kind": "value", "value": 1}},
        ],
    )
    result = asyncio.run(IndependentRunner(image).run(workspace, suite))
    assert [case["outcome"] for case in result.test_report["tests"]] == [
        "passed",
        "passed",
        "error",
    ]
    assert result.case_evidence[-1]["observation"]["status"] == "timeout"


def test_monorepo_dataclass_contract(image, workspace):
    source = workspace / "backend" / "src"
    source.mkdir(parents=True)
    (source / "candidate.py").write_text("""from dataclasses import dataclass
@dataclass
class Answer:
    doubled: int

def answer(value):
    return Answer(value * 2)
""")
    suite = profile(
        source_directory="backend/src",
        result_encoding="dataclass",
        cases=[
            {"id": "dataclass", "args": [5], "expect": {"kind": "value", "value": {"doubled": 10}}}
        ],
    )
    result = asyncio.run(IndependentRunner(image).run(workspace, suite))
    assert result.test_report["tests"][0]["outcome"] == "passed", result
    with pytest.raises(ValidationError):
        profile(source_directory="../outside")
