# Real public repository reproduction

CodeHound includes a pinned reproduction of the genuine upstream
[pypa/packaging PR #925](https://github.com/pypa/packaging/pull/925),
**Correct regex for metadata 'name' format**. The upstream author `di` submitted
the fix on August 21, 2025; it was merged the same day. This is a historical
upstream patch used to exercise the real repository workflow. Its authorship
method is unknown; GitHub metadata does not establish whether AI was used. It is not
an AI agent benchmark, an adversarial patch, or evidence of a detection rate.

The bug is narrow: `canonicalize_name(name, validate=True)` accepted an otherwise
valid package name followed by a newline because the validation regex ended in
`$`. The patch changes that anchor to `\Z`, making the validator reject the
trailing newline. Both pinned revisions expose the same callable API and return
a JSON string for accepted inputs or raise `packaging.utils.InvalidName`.

| Evidence | Pinned value |
| --- | --- |
| Repository | `pypa/packaging` |
| PR | `925`, actually closed and merged |
| Baseline / merge base | `033854a05229074ddb191d67da1f8e0165e665da` |
| Candidate / PR head | `ca4e142149c04000c91c9f16642452e999ee04ba` |
| Changed files | `src/packaging/utils.py`, `tests/test_utils.py` |
| Profile | `packaging-name-validation` |
| Existing test selection | baseline `tests/test_utils.py` |

The fixture at `backend/fixtures/real_repository/packaging-pr-925/` includes
the original public GitHub REST `pull-request.json`, the immutable comparison's
`pinned.diff`, and an operator-owned `snapshot.json`. The snapshot includes their
SHA-256 hashes, source URLs, real PR metadata, and exact base/head identities.
The diff can be checked against the
[immutable GitHub comparison](https://github.com/pypa/packaging/compare/033854a05229074ddb191d67da1f8e0165e665da...ca4e142149c04000c91c9f16642452e999ee04ba),
and metadata against the
[GitHub REST PR response](https://api.github.com/repos/pypa/packaging/pulls/925).
Live metadata may change; the checked-in response records the capture.

The normal HTTP intake and capture command accept open public PRs and closed
public PRs whose GitHub metadata records `merged: true`. Closed unmerged PRs
remain unsupported. This fixture was captured before historical intake support
and preserves the actual `closed` state; the evaluator accepts it unchanged.
No upstream Python was imported or executed while capturing metadata.

To verify this historical PR in the dashboard, select **New verification** and
paste `https://github.com/pypa/packaging/pull/925`, describe the newline validation
requirement, capture the PR, and select the **packaging PR #925 name validation
regression** profile. The repository browser continues to list open PRs. Capture
preserves GitHub's actual closed/merged status and compares the PR head with the
comparison merge base, rather than replacing the head with the merge commit.

## Reproduce

From the repository root, install the backend development environment as
described in the README and build the trusted Python image:

```sh
docker build -t codehound-python:local backend/test-environments/python
CODEHOUND_IMAGE_ID=$(docker image inspect codehound-python:local --format '{{.Id}}')

PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.real_repository \
  --snapshot backend/fixtures/real_repository/packaging-pr-925/snapshot.json \
  --profile packaging-name-validation \
  --image-id "$CODEHOUND_IMAGE_ID" \
  --output data/packaging-real-repository.json
```

The output path must be new; choose another filename for repeat runs. Git fetches
the two immutable public revisions without credentials, hooks, installation, or
repository scripts. All Python source inspection and execution uses restricted
Docker containers with read-only checkouts and networking disabled. The trusted
image needs the pinned pytest and Ruff versions in its Dockerfile. The selected
packaging test file needs no other third-party dependency.

To capture fresh public metadata through the normal intake collector, then use
the resulting snapshot instead of the fixture:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.repositories.capture \
  https://github.com/pypa/packaging/pull/925 \
  --output data/packaging-live-intake-2026-10-06.json
```

This capture performs read-only GitHub requests and executes no repository code.
It verifies the file inventory, pinned diff and SHA-256, and checks metadata and
repository visibility again before accepting the snapshot. Historical support
keeps the existing public-head and deleted-fork restrictions.

The command runs independent visible and edge-case suites, structural test
review, Python impact inspection, differential Ruff checks, and the baseline
repository test selection against both revisions. It exports requirements mapped
to individual independent cases and the upstream provenance alongside the results.

## Observed smoke result

On October 6, 2026, a live GitHub checkout and Docker run using image
`sha256:199083f1031badac261212e56122476a98696367cbf30b5bb1867e5ffbbb3f4a`
observed:

| Check | Baseline | Candidate | Comparison |
| --- | --- | --- | --- |
| Independent visible cases | 2/3 pass | 3/3 pass | 1 improvement, 0 regressions |
| Independent edge cases | 11/13 pass | 13/13 pass | 2 improvements, 0 regressions |
| Frozen baseline `test_utils.py` | passes | passes | No behavior change observed |

The profile checks newline rejection, valid-name normalization, representative
existing invalid names, and behavior with validation disabled. All 16 independent
cases map to four explicit operator requirements. The bundled `hidden` suite is
public; its name identifies an independent edge-case suite, not secret tests.

The existing repository tests are frozen from the baseline, including available
ancestor fixture/package files. The candidate's added newline tests and pytest
configuration are excluded. The runner clears pytest's installed `packaging`
modules before loading the selected tests and checks the target package's import
origins under the mounted revision. Missing or wrong-origin imports make that
evidence inconclusive. Repository tests still share a process with repository
code and are labeled `repository_controlled_in_process_pytest`; they never affect
the independent assessment or requirement coverage.

The upstream test change legitimately parameterizes an existing test and adds
coverage. Structural review may flag changed assertion expressions for human
review. These findings and Ruff diagnostics are source observations, not labels
that the patch is malicious or incorrect. Passing mapped examples supports only
the observations tested, and confidence remains unscored. Behavior beyond the
selected contract and tests remains unverified.

## Automated checks

Offline deterministic tests verify the captured metadata/diff hashes, exact
revision identities, requirement mappings, CLI wiring, and the normal API intake
and export flow using the genuine historical metadata. They perform no network
requests and execute no upstream code:

```sh
backend/.venv/bin/python -m pytest backend/tests/test_real_repository.py -q
```

To explicitly enable the live public Git checkout and Docker smoke:

```sh
CODEHOUND_TEST_IMAGE_ID="$CODEHOUND_IMAGE_ID" \
CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS=1 \
backend/.venv/bin/python -m pytest backend/tests/test_real_repository.py -q
```

The live test is skipped unless both settings are present. It checks the expected
three independent improvements, no observed regressions, all 16 candidate cases,
passing frozen repository tests, and the accompanying source-analysis artifacts.
