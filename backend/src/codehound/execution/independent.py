"""Judge JSON observations outside candidate containers, using operator-owned expectations."""

import asyncio
import base64
import binascii
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

from codehound.execution.docker import ContainerRunner, ExecutionResult
from codehound.execution.profiles import TrustedSuite
from codehound.execution.protocol import load_evidence

CALL_PREFIX = "CODEHOUND_CALL_V1:"
JSON_VALUE = TypeAdapter(JsonValue)


def decode_response(stdout, token):
    marker = CALL_PREFIX + token + ":"
    frames = [line[len(marker) :] for line in stdout.splitlines() if line.startswith(marker)]
    if len(frames) != 1:
        return None, "missing_response" if not frames else "duplicate_responses"
    try:
        response = load_evidence(base64.b64decode(frames[0], validate=True), limit=16384)
        if not isinstance(response, dict):
            raise ValueError("Response must be an object")
        kind = response["kind"]
        fields = {
            "returned": {"kind", "value"},
            "raised": {"kind", "exception"},
            "adapter_error": {"kind", "message"},
        }
        if not isinstance(kind, str) or set(response) != fields.get(kind):
            raise ValueError("Invalid response envelope")
        if kind == "returned":
            JSON_VALUE.validate_python(response["value"], strict=True)
            json.dumps(response["value"], allow_nan=False)
        elif kind == "raised":
            if not isinstance(response["exception"], str) or len(response["exception"]) > 200:
                raise ValueError("Invalid exception")
        elif not isinstance(response["message"], str) or len(response["message"]) > 2000:
            raise ValueError("Invalid adapter error")
    except (ValueError, TypeError, KeyError, RecursionError, binascii.Error):
        return None, "invalid_response"
    return response, None


def json_equal(left, right):
    # Python considers True == 1; the JSON behavior contract must distinguish them.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            json_equal(left[key], right[key]) for key in left
        )
    return left == right


def judge(case, observation):
    if observation.status != "completed" or observation.exit_code != 0:
        return "error", f"Candidate execution: {observation.status}, exit {observation.exit_code}."
    response = observation.call_response
    if observation.evidence_error or response is None:
        return "error", observation.evidence_error or "missing_response"
    if response["kind"] == "adapter_error":
        return "error", "Candidate could not satisfy the JSON adapter contract."
    if case.expect.kind == "value":
        passed = response["kind"] == "returned" and json_equal(response["value"], case.expect.value)
    else:
        expected = case.expect.exception
        expected = expected if "." in expected else "builtins." + expected
        passed = response["kind"] == "raised" and response["exception"] == expected
    return (
        ("passed", "")
        if passed
        else ("failed", "Observed behavior differs from the independent expectation.")
    )


class IndependentRunner(ContainerRunner):
    """One disposable, offline candidate container per case; all judgments stay outside."""

    async def observe(self, workspace, profile, case):
        token = uuid4().hex
        payload = (
            json.dumps(
                {
                    "module": profile.module,
                    "function": profile.function,
                    "args": case.args,
                    "kwargs": case.kwargs,
                    "source_directory": profile.source_directory,
                    "result_encoding": profile.result_encoding,
                },
                allow_nan=False,
            ).encode()
            + b"\n"
        )
        if len(payload) > 32768:
            raise ValueError("Candidate request exceeds 32 KiB.")
        result = await self.run_container(
            workspace,
            [(Path(__file__).parent / "adapters", "/harness")],
            ["python", "-I", "/harness/call_adapter.py", token],
            input_data=payload,
        )
        response, error = decode_response(result.stdout, token)
        output = result.stdout
        if response is not None:
            output = "\n".join(
                line
                for line in output.splitlines()
                if not line.startswith(CALL_PREFIX + token + ":")
            )
        return replace(
            result,
            stdout=output,
            call_response=response,
            evidence_error=error,
            evidence_source="external_json_assertions",
        )

    async def run(self, workspace: Path, profile: TrustedSuite):
        # Freeze the trusted profile before sending any inputs to candidate processes.
        profile = TrustedSuite.model_validate_json(profile.canonical_bytes())
        evaluator = hashlib.sha256(
            Path(__file__).read_bytes()
            + (Path(__file__).parent / "adapters" / "call_adapter.py").read_bytes()
            + (Path(__file__).parent / "protocol.py").read_bytes()
            + profile.canonical_bytes()
        ).hexdigest()
        runner = IndependentRunner(
            self.image_id,
            timeout_seconds=min(profile.timeout_seconds, self.timeout),
            output_limit=16384,
        )
        started = time.monotonic()
        tests, evidence = [], []
        status = "completed"
        try:
            async with asyncio.timeout(profile.suite_timeout_seconds):
                for case in profile.cases:
                    observation = await runner.observe(workspace, profile, case)
                    outcome, message = judge(case, observation)
                    tests.append(
                        {
                            "nodeid": profile.name + "::" + case.id,
                            "outcome": outcome,
                            "duration_seconds": observation.duration_seconds,
                            "message": message,
                        }
                    )
                    evidence.append({"case_id": case.id, "observation": observation.to_dict()})
        except TimeoutError:
            status = "timeout"
        completed = len(tests)
        tests.extend(
            {
                "nodeid": profile.name + "::" + case.id,
                "outcome": "not_run",
                "duration_seconds": 0,
                "message": "Suite deadline exceeded.",
            }
            for case in profile.cases[completed:]
        )
        exit_code = 2 if status == "timeout" else int(any(t["outcome"] != "passed" for t in tests))
        report = {
            "schema_version": 1,
            "exit_code": exit_code,
            "collected": [t["nodeid"] for t in tests],
            "tests": tests,
            "collection_errors": [],
        }
        return ExecutionResult(
            status,
            exit_code,
            "",
            "",
            round(time.monotonic() - started, 3),
            self.image_id,
            False,
            False,
            profile.suite_timeout_seconds,
            test_report=report,
            evaluator_sha256=evaluator,
            evidence_source="external_json_assertions",
            case_evidence=evidence,
        )
