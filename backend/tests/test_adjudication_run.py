import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_adjudication import bundle, review

from codehound.benchmark.adjudication import canonical_sha256
from codehound.benchmark.adjudication_run import invoke, run, validate_trace


def trace(context="fresh-context", tool=None, answer=None):
    events = [{"type": "thread.started", "thread_id": context}, {"type": "turn.started"}]
    if tool:
        events.append({"type": "item.completed", "item": {"type": tool}})
    events += [
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(event) for event in events)


@pytest.mark.parametrize(
    "tool", ["command_execution", "mcp_tool_call", "web_search", "file_change"]
)
def test_tool_use_invalidates_blinding(tool):
    with pytest.raises(ValueError, match="tool"):
        validate_trace(trace(tool=tool))


def test_failed_or_multiple_sessions_are_not_a_blinded_review():
    with pytest.raises(ValueError):
        validate_trace(trace() + '\n{"type":"turn.failed"}')
    with pytest.raises(ValueError):
        validate_trace(trace() + '\n{"type":"thread.started","thread_id":"another"}')


def test_identical_prompt_fresh_contexts_and_recorded_inspection(monkeypatch, tmp_path):
    supplied = bundle()
    response = review(supplied)
    answer_keys = [
        "verdict",
        "confidence",
        "evidence",
        "verified_material_ids",
        "actually_verified",
        "uncertainty",
    ]
    calls = []

    def fake_process(command, **kwargs):
        calls.append((command, kwargs))
        assert "--ignore-user-config" in command and "--ephemeral" in command
        assert "project_doc_max_bytes=0" in command
        working = kwargs["cwd"]
        assert list(working.iterdir()) == [working / "schema.json"]
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({key: response[key] for key in answer_keys})
        )
        return SimpleNamespace(
            returncode=0,
            stdout=trace(
                f"context-{len(calls)}", answer={key: response[key] for key in answer_keys}
            ),
            stderr="",
        )

    monkeypatch.setattr("codehound.benchmark.adjudication_run.subprocess.run", fake_process)
    a = invoke(supplied, "model-a", "A", tmp_path)
    b = invoke(supplied, "model-b", "B", tmp_path)
    assert calls[0][1]["input"] == calls[1][1]["input"]
    assert a["context_id"] != b["context_id"]
    assert a["model_id"] == "model-a" and b["model_id"] == "model-b"
    assert invoke(supplied, "model-a", "A", tmp_path) == a
    assert len(calls) == 2
    with pytest.raises(ValueError, match="different"):
        invoke(supplied, "changed-model", "A", tmp_path)
    stored = tmp_path / f"{supplied['case_id']}-A.trace.jsonl"
    stored.write_text(trace(tool="command_execution"))
    with pytest.raises(ValueError, match="trace changed"):
        invoke(supplied, "model-a", "A", tmp_path)


def test_run_refuses_single_model_and_unbounded_parallelism(tmp_path):
    with pytest.raises(ValueError, match="distinct"):
        run([bundle()], tmp_path, "same", "same")
    with pytest.raises(ValueError, match="two"):
        run([bundle()], tmp_path, "a", "b", workers=10)


@pytest.mark.parametrize("identifier", ["../outside", "/absolute", "nested/file", "line\nbreak"])
def test_case_identifier_cannot_escape_review_output(tmp_path, identifier):
    delivered = bundle()
    delivered["case_id"] = identifier
    delivered["material_sha256"] = canonical_sha256(delivered)
    with pytest.raises(ValueError, match="filename"):
        invoke(delivered, "model", "A", tmp_path)
