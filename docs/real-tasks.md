# Additional pinned historical tasks

These two tasks reproduce distinct merged upstream fixes in
[`mahmoud/boltons`](https://github.com/mahmoud/boltons), a Python utility library.
Both target Boltons functions using standard-library dependencies, with JSON scalar arguments and
string results. No additional container dependency is needed. They complement
the existing packaging name-validation example.

| Task | Historical PR | Independent contract |
| --- | --- | --- |
| `boltons-bytes-boundaries` | [#403, exact powers of 1024](https://github.com/mahmoud/boltons/pull/403) | `bytes2human` selects the next unit at an exact threshold; representative negative magnitudes, precision and non-boundary values retain their behavior. |
| `boltons-singular-double-s` | [#418, singular words ending in ss](https://github.com/mahmoud/boltons/pull/418) | `singularize` preserves already-singular ss endings and their case; real plurals and selected existing inflections remain supported. |

The first patch changes a numeric threshold comparison. The second adds a
linguistic suffix guard. The contracts test different behaviors rather than
repeating the same bug under new inputs. They remain narrow examples: larger
units and nonfinite values are outside the byte-format contract; ambiguous
single-s words and complete English inflection are outside the word contract.

Each `backend/fixtures/real_repository/boltons-pr-<number>/` directory contains
the original REST PR response, a snapshot captured by the normal historical PR
intake path, the exact pinned diff, and a summary of an actual container run.
Snapshots preserve the genuine closed/merged state, recorded author account,
immutable identities, public source URLs, and metadata/diff SHA-256 hashes.
Compact observed summaries retain the full execution artifact's SHA-256,
independent suite/profile and evaluator identities, and frozen repository-test
configuration, evaluator and input-file hashes. The profile binding combines
public profile metadata, requirement mappings, suite hashes and repository-test
configuration. Offline tests compare that binding and evaluator/configuration
hashes to the current operator profiles and harness files; changed inputs require
fresh observed evidence. Frozen input hashes identify the exact baseline tests
and ancestor fixture/package files used in the original run.
GitHub account metadata does not establish whether AI assistance was used or
whether a patch constitutes independently reviewed ground truth. These are
historical upstream reproductions, not an AI-agent benchmark, labeled negative
dataset, or basis for a detection rate.

| PR | Baseline / merge base | Candidate / PR head |
| --- | --- | --- |
| 403 | `207651ee6055aabd0d9cdeac2e00140cdc208d44` | `bfeca9dd304745e0faf62e78351f401bbdd7b05d` |
| 418 | `979fa9b613fa8c0a455ae16ea6f2ec91c11ecafe` | `168ef1575e2ccfb308d7892548c41d24fd251b26` |

The capture executes no upstream Python. Execution uses the existing independent
JSON runner inside offline restricted Docker containers. Expected results stay
in the operator controller. Both profiles map every visible and public edge
case to explicit requirements. Frozen baseline `tests/test_strutils.py` is also
run against both revisions, excluding the candidate's new tests and config.
That repository-controlled in-process pytest evidence is separate from the
independent assessment.

## Reproduce

From the repository root, use a newly built trusted image ID with pinned pytest
and Ruff. Set `CODEHOUND_IMAGE_ID` to the immutable ID, then run:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.real_repository \
  --snapshot backend/fixtures/real_repository/boltons-pr-403/snapshot.json \
  --profile boltons-bytes-boundaries \
  --image-id "$CODEHOUND_IMAGE_ID" --output data/boltons-403-result.json

PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.real_repository \
  --snapshot backend/fixtures/real_repository/boltons-pr-418/snapshot.json \
  --profile boltons-singular-double-s \
  --image-id "$CODEHOUND_IMAGE_ID" --output data/boltons-418-result.json
```

The output path must be new. The CLI runs independent suites, mapped requirement
evidence, frozen repository tests, structural test review, Python impact and
differential Ruff analysis. It preserves captured upstream provenance. Both
profiles are also selectable after ordinary dashboard intake of their PR URLs.

## Observed run

On October 6, 2026 (America/New_York), trusted image
`sha256:199083f1031badac261212e56122476a98696367cbf30b5bb1867e5ffbbb3f4a`
produced the following observations. Counts refer only to these supplied cases.

| Task | Baseline independent passes | Candidate independent passes | Improvements | Frozen baseline repository tests |
| --- | --- | --- | --- | --- |
| byte thresholds | 6/13 | 13/13 | 7, no observed regressions | 16 pass on both revisions |
| singular ss endings | 7/15 | 15/15 | 8, no observed regressions | 17 pass on both revisions |

Ruff completed both comparisons with 143 existing findings and no new findings
in each. This static result does not establish runtime correctness. Existing
tests passing on the buggy baseline demonstrate why the independent contract
cases add useful evidence. Confidence remains unscored and complete task
adherence remains unverified.

Offline provenance/profile tests perform no network requests or upstream code
execution:

```sh
backend/.venv/bin/python -m pytest backend/tests/test_additional_real_tasks.py -q
```

Enable the actual public Git checkout and container tests explicitly:

```sh
CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS=1 \
CODEHOUND_TEST_IMAGE_ID="$CODEHOUND_IMAGE_ID" \
backend/.venv/bin/python -m pytest backend/tests/test_additional_real_tasks.py -q
```
