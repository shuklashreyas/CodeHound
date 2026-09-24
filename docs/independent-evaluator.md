# Independent JSON behavior evaluation

This mode keeps expectations and assertion logic outside the candidate container.
It supports a deliberately narrow contract: a Python callable in the checked-out
repository accepts JSON-compatible positional/keyword arguments and returns a
JSON-compatible value, or raises an expected exception.

```text
Operator-owned profile (inputs + expectations)
        ↓ send one input only
Disposable offline candidate container
        ↓ observed value or exception
External evaluator compares against its retained expectation
        ↓ named outcome, before/after comparison, evidence
```

The container mounts candidate code and the small transport adapter. It does not
mount test profiles, expected answers, pytest tests, host credentials, or the Docker
socket. Every case gets a fresh container. Assertions execute in the CodeHound
controller, which never imports or evaluates candidate Python. Responses are
bounded JSON observations; the controller never unpickles, executes, or imports
returned data. Altering candidate pytest configuration or monkeypatching pytest
cannot change these external judgments.

## Profile format

Profiles are written and selected by the operator, outside the submitted PR:

```json
{
  "schema_version": 1,
  "name": "pagination-independent",
  "module": "pagination",
  "function": "pages",
  "timeout_seconds": 5,
  "cases": [
    {
      "id": "partial-page",
      "args": [[1, 2, 3], 2],
      "expect": {"kind": "value", "value": [[1, 2], [3]]}
    },
    {
      "id": "invalid-size",
      "args": [[1, 2], 0],
      "expect": {"kind": "exception", "exception": "ValueError"}
    }
  ]
}
```

`kwargs` is optional. `source_directory` defaults to `src`; use `backend/src`
for a monorepo. It must remain inside the workspace. `result_encoding` defaults
to `json`; `dataclass` converts a returned dataclass to a JSON object inside the
container. Exception names without a module prefix refer to builtins.
JSON object key ordering does not affect equality. Booleans differ from numbers;
finite integers and floats compare numerically. Non-JSON outputs, missing responses,
ambiguous responses, import/setup failures, and timeouts produce errors rather
than passes. An unexpected value or exception produces a behavioral failure.
The target must load from `/workspace`, including its `src/` directory, so a
package accidentally resolved from the image cannot stand in for candidate code.

Profiles are capped at 32 KiB and 50 cases. A case has a 1–10 second execution
limit; the complete suite has a 120-second deadline. Each observation is capped at
16 KiB. The report retains observed outputs and runtime evidence, hashes the
profile/evaluator, and labels its source `external_json_assertions`. Expected
answers are not copied into execution reports.

## Run it

Use the trusted image from the root README:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.demo \
  --mode independent --image "$CODEHOUND_IMAGE_ID" \
  --output data/independent-demo.json

PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.verify \
  --mode independent --snapshot data/pr-evidence.json \
  --visible-tests /absolute/path/to/visible-profile.json \
  --hidden-tests /absolute/path/to/independent-profile.json \
  --image-id "$CODEHOUND_IMAGE_ID" --output data/independent-comparison.json
```

The demo's profiles are public fixtures, not a secret benchmark. Production
profiles should be maintained separately from candidate repositories. The same
frozen profile is used for both pinned revisions.

## What this establishes

This checks observable behavior under a specified function-to-JSON contract. The
candidate controls its response and may hardcode particular inputs; diverse,
independently retained cases are still necessary. A passing response is not proof
of general correctness, implementation quality, unobserved side effects, or all
issue requirements. No automatic test generation or calibrated confidence is
claimed. Stateful services, arbitrary test frameworks, objects without an explicitly supported encoding, and
multi-step scenarios require additional adapters.

Docker provides the process/filesystem boundary, not a formal security guarantee.
Untrusted workloads still require hardened, dedicated production workers. The
legacy pytest mode remains available and is explicitly labeled as in-process.
