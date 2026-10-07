# Wrong patches that pass tests on a real public repository

Two deliberate bad patches to the pinned `pypa/packaging` baseline pass every
visible example and all 54 frozen existing repository tests. CodeHound's
independent cases reject both. The actual upstream PR head passes all three kinds
of checks. This demonstrates the value of independent behavioral checks using a
real codebase and bug, while keeping the negative examples explicitly synthetic.

The experiment uses the same [upstream PR #925](https://github.com/pypa/packaging/pull/925),
immutable revisions and operator profile documented in
[real-repository.md](real-repository.md). It reuses CodeHound's benchmark runner;
the experiment CLI prepares bounded repository copies and adds frozen repository
test evidence. No upstream source is imported or executed on the host.

| Candidate | Provenance and deliberate behavior |
| --- | --- |
| `upstream-correct` | Actual upstream PR head `ca4e142149c04000c91c9f16642452e999ee04ba`; authorship method is unknown. |
| `overfit` | Operator-authored patch to baseline `033854a05229074ddb191d67da1f8e0165e665da`; special-cases only the visible `Friendly_Bard\n` input and leaves the underlying newline bug. |
| `regressive` | Operator-authored patch to the same baseline; fixes validated newline inputs but also rejects newline inputs when `validate=False`, changing existing API behavior. |

The two patch files are retained under
`backend/fixtures/real_repository/packaging-pr-925/mutations/`. Each mutant is a
temporary copy of the complete real baseline with one explicit patch applied to
`src/packaging/utils.py`. The upstream control is a copy of the actual pinned head.
Mutants are identified by workspace and patch SHA-256 hashes; they have no
invented Git commit identity or agent attribution. The artifact also records the
target source-file hash, evaluator identity, profile hashes and container image.

The corpus is marked `synthetic_demo` and uses only the `demo` split. The labels
are operator-supplied judgments of this narrow function contract: the upstream
control is `valid`, and each mutation is deliberately `invalid` for the stated
reason. They are not human adjudications, general PR correctness labels, or
measurements of real AI-generated patches. Public tests and two hand-constructed
negatives cannot establish a detection rate on an agent population.

## Observed result

A live GitHub checkout and restricted Docker run on October 6, 2026 observed:

| Candidate | Visible cases | Independent cases | Frozen baseline repository tests | Independent decision |
| --- | --- | --- | --- | --- |
| Upstream control | 3/3 pass | 13/13 pass | 54/54 pass | Accept configured contract |
| Overfit mutation | 3/3 pass | 11/13 pass | 54/54 pass | Reject: unresolved task behavior |
| Regression mutation | 3/3 pass | 12/13 pass | 54/54 pass | Reject: observed regression |

The overfit patch still returns `"a\n"` for `canonicalize_name("A\n", validate=True)`
and `"release-v2-9\n"` for `canonicalize_name("Release.v2_9\n", validate=True)`.
Both should raise `packaging.utils.InvalidName`. Its visible example passes because
the implementation recognizes that exact string.

The regression patch correctly rejects validated trailing newlines but raises
`InvalidName` for `canonicalize_name("MiXeD_Name\n", validate=False)`. The baseline
returns `"mixed-name\n"`; the configured contract preserves that optional-validation
behavior. CodeHound records both the two fixed hidden cases and this regression.

For these three deliberately selected candidates, visible-only checking catches
**0 of 2** bad patches, independent checking catches **2 of 2**, and the upstream
control is accepted. These are demonstration counts, not a general accuracy claim.

Repository tests are frozen from the baseline and run against each candidate.
The same 54 tests pass for all three because the baseline suite lacks the newline
coverage introduced by the upstream PR. The candidate's own tests do not replace
those frozen tests. Their lower-trust results never affect independent decisions.
Independent expectations remain in the operator process; candidate containers
receive input values and return JSON observations for host assertions.

## Reproduce

Build the trusted image as described in [real-repository.md](real-repository.md),
then run from the repository root:

```sh
CODEHOUND_IMAGE_ID=$(docker image inspect codehound-python:local --format '{{.Id}}')
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.real_repository_experiment \
  --snapshot backend/fixtures/real_repository/packaging-pr-925/snapshot.json \
  --mutations backend/fixtures/real_repository/packaging-pr-925/mutations \
  --image-id "$CODEHOUND_IMAGE_ID" \
  --output data/packaging-adversarial-experiment.json
```

Git reads immutable public revisions without credentials, repository hooks or
installation. Workspace preparation copies data, excludes `.git`, rejects symlinks
and special files, and caps each copy at 32 MiB and 10,000 entries. Trusted patches
are capped at 32 KiB and checked before applying. All Python function calls and
repository tests run inside restricted Docker containers with networking disabled.

The output path must be new. The CLI creates and atomically updates partial
checkpoints so interrupted, failed or incomplete work remains explicit. The outer
artifact's `status` covers the entire experiment; nested benchmark completion
covers independent cases only. Repository evidence is attached to each candidate
row afterward. Identity changes invalidate judgments rather than retaining acceptance.

The JSON contains per-case outcomes and observed responses, before/after
comparisons, operator requirements, label rationales, source/patch identities,
benchmark demonstration counts and repository-test import provenance.
It uses the same trusted image as the public-repository smoke:
`sha256:199083f1031badac261212e56122476a98696367cbf30b5bb1867e5ffbbb3f4a`.

Offline safeguard tests and an explicitly enabled live test are available:

```sh
backend/.venv/bin/python -m pytest backend/tests/test_real_experiment.py -q

CODEHOUND_TEST_IMAGE_ID="$CODEHOUND_IMAGE_ID" \
CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS=1 \
backend/.venv/bin/python -m pytest backend/tests/test_real_experiment.py -q
```

The live test asserts the observed acceptance/rejection decisions, exact hidden
failure counts, all 54 frozen tests per revision, patch hashes, and demonstration
counts. It is skipped unless both settings are supplied.
