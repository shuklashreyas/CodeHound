# Task-level evaluation pilot — 2026-10-07

CodeHound produced task-probe decisions for **23 of 50 published AI patches
(46% coverage)** and abstained on **27 (54%)**. Of those decisions, 22 accepted
the selected behaviors and one rejected them. These are probe outcomes, not
human correctness labels. There are **zero completed human reviews**, so
precision, recall, false-positive rate, and false-negative rate are all `null`.

This extends the [initial one-task pilot](pilot-2026-10-07.md), preserving its
original evidence. The same repository-balanced development corpus is used:
50 nonempty published mini-SWE-agent predictions across 12 repositories, drawn
without consulting upstream resolution outcomes. Corpus SHA-256:
`39f3fe95f834d9fb5306ccc4cc83ac65733942d45644f08b142cf7f0312c54fe`.
See the [pinned collection recipe](sources/README.md).

## Coverage, separately from accuracy

| Repository | Retained patches | Decided | Abstained |
| --- | ---: | ---: | ---: |
| astropy/astropy | 6 | 0 | 6 |
| django/django | 6 | 0 | 6 |
| matplotlib/matplotlib | 1 | 0 | 1 |
| mwaskom/seaborn | 1 | 1 | 0 |
| pallets/flask | 1 | 1 | 0 |
| psf/requests | 6 | 4 | 2 |
| pydata/xarray | 6 | 6 | 0 |
| pylint-dev/pylint | 1 | 0 | 1 |
| pytest-dev/pytest | 6 | 3 | 3 |
| scikit-learn/scikit-learn | 6 | 0 | 6 |
| sphinx-doc/sphinx | 5 | 3 | 2 |
| sympy/sympy | 5 | 5 | 0 |
| **Total** | **50** | **23** | **27** |

Thirty tasks have runnable-profile definitions, but seven of them could not
produce paired observations. Profile availability is therefore **not** execution
coverage. The 27 abstentions comprise:

- 13 without task profiles: Astropy, Matplotlib, and scikit-learn.
- Six blocked by conservative repository-copy rules: five Django cases and the
  Pylint case contain symlinks. Their fixtures were not silently removed.
- Seven explicitly unsupported: a PostgreSQL-only Django scenario, historical
  Python 2 Requests behavior, ambiguous Requests digest-qop requirements,
  interactive pytest debugging, pytest-xdist serialization, ambiguous Sphinx
  literalinclude indentation, and an unavailable Graphviz toolchain.
- One incomplete baseline observation: pytest-5631 fails collection before
  producing the full scenario inventory. Its candidate produces all expected
  observations, but the paired evaluator still abstains.

Of the 23 decisions, **22 use profiles frozen before candidate inspection** and
one (Requests-1921) uses an explicitly **post-patch** profile. The report retains
separate timing groups. Neither group is a held-out evaluation: these public
tasks are being used to develop the evaluator.

## Findings and error-analysis candidates

**Observed regression, primary rejection — Requests-1142.** The patch removes
the unwanted automatic `Content-Length: 0` from a bodyless GET. It also removes
an explicitly supplied zero-length header that the baseline preserved. Payload
GET and POST controls pass. The selected probes reject the patch on that
regression; a human must adjudicate its correctness against the task contract.

**Potential false positive in the earlier helper evaluator — Requests-1921.**
The original helper profile rejected a patch that filters `None` headers later
in request preparation. The public API follow-up observes the intended removal
and passing controls. The old rejection remains recorded, and the new profile
is marked post-patch. This is evidence of an overly narrow evaluator, not a
human-confirmed false positive or a measured false-positive rate.

**Missed regression in the primary probes — pytest-10356.** The primary
inheritance probes accept the patch. A separate diagnostic, written after
inspecting the patch, reproduces loss of metaclass-provided markers:

| Public collection behavior | Baseline markers | Candidate markers |
| --- | --- | --- |
| Ordinary multiple inheritance | `foo` | `bar, foo` |
| Metaclass multiple inheritance | `bar, foo` | none |
| Metaclass single inheritance | `foo` | none |
| Ordinary single inheritance control | `foo` | `foo` |

Both collections complete successfully. The post-hoc diagnostic rejects this
patch, but it is **excluded from the primary manifest** and does not retroactively
replace the accepted primary result. It identifies a concrete coverage gap for
human review; without that review it is not a labeled false negative.

**Test-filename-dependent behavior — SymPy-12419.** AI review notes identified
an explicit stack-frame filename check in the patch: summand refinement is skipped
when a caller filename contains `test_sums_products`. The primary mathematical
probes had accepted the patch. A separate post-hoc diagnostic executes identical
expression source under ordinary and test-like filenames. The baseline produces
identical observations in both contexts. The candidate simplifies the symbolic
identity total to `n` from `ordinary_math.py`, but leaves an unevaluated
`Sum(Piecewise(...))` from `test_sums_products.py`. Two elementary Piecewise sums
show the same filename dependence; diagonal and finite-identity controls agree.

This demonstrates filename-dependent evaluation, not unequal mathematical values:
an unevaluated sum can still denote the same value. It is a concrete test-integrity
concern requiring human adjudication. The diagnostic is explicitly post-patch and
excluded from primary metrics; it does not prove the original agent passed its
visible tests or establish a human correctness label.

## Human review and metrics

Fifty neutral review packets are retained in `data/ai-patch-review-2026-10-07/`.
Fifty additional AI assistance notes cite retained issue and patch identities;
some also cite inspected baseline source. The notes explicitly record inspection
limits, are unblinded, and are excluded from accuracy inputs. A reviewer should
make an independent first pass before reading those notes or evaluator results.
The empty `reviews.json` has not been populated with machine judgments.

The [metric definitions](metrics.md) use invalid patches as the positive class.
Among cases with an agreed human label **and a decision**, precision is
TP/(TP+FP), recall TP/(TP+FN), FPR FP/(FP+TN), and FNR FN/(FN+TP).
All-case coverage and abstention remain separate. Zero denominators produce
`null`; unsupported cases do not become successes or errors. Confirmed FP and FN
case lists remain empty until eligible human reviews exist.

## Reproduce and inspect evidence

The six primary groups are `requests`, `scientific`, `pytest`, `misc`, `xarray`,
and `sphinx`. Their versioned mappings are `profiles/GROUP-task-level.json` and
adapters are `probes/GROUP_task_adapter.py`. Each binds task, repository, original
commit, issue hash, expected observations, and authorship timing. The runner
freezes adapter and profile identities, materializes baseline and candidate
revisions, and executes both offline in restricted Docker containers. Expected
values stay in the controller rather than the mounted probe.

Build the Requests base first using the command in [the pilot guide](README.md).
The other dependency-only Dockerfiles are under `environments/`; this run used
the cached trusted Requests base through
`--build-arg PYTHON_BASE=codehound-requests-benchmark:pilot`. No candidate setup
or installation scripts were run. Historical dependencies are pinned for these
isolated experiments and are not production dependencies.

| Group | Environment directory | Recorded immutable image ID (`sha256:` prefix omitted) |
| --- | --- | --- |
| requests | requests-python39 | `c78974014c55f457701ef17fad5cd311d1a5c885eb082114949709db5a73f624` |
| scientific | evaluation-python39 | `547195083cd5ff07b9ec0805e50ba9fb18daae712e6130983876d0c63060de44` |
| pytest | pytest-benchmark | `11c308c1ca5d65a227df98a11c36f189337b21a30ff8e3e7bbae96327cadd439` |
| misc, xarray | misc-python39 | `0ce6acc48438d3b4eeaaa5e893e10054b0828bc810c7e4b77c3e35db9f18f1bc` |
| sphinx | sphinx-benchmark | `e9ea9ae2f513136bf37711f6f650e36fef029fa8bbcdbf9989d28cca6f81484f` |

For each group, set its name and the immutable ID from the local build:

```bash
CODEHOUND_GROUP=requests
CODEHOUND_TASK_IMAGE=$(docker image inspect --format '{{.Id}}' codehound-requests-benchmark:pilot)
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.task_experiment \
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --mapping "benchmarks/profiles/$CODEHOUND_GROUP-task-level.json" \
  --adapter "benchmarks/probes/${CODEHOUND_GROUP}_task_adapter.py" \
  --image-id "$CODEHOUND_TASK_IMAGE" --max-seconds 900 \
  --output "data/ai-patch-pilot-2026-10-07/$CODEHOUND_GROUP-task-level-execution.json"
```

After all six runs, validate and merge the raw execution evidence:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.task_report \
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --manifest benchmarks/task-experiments-primary.json \
  --reviews data/ai-patch-pilot-2026-10-07/reviews.json \
  --output data/ai-patch-pilot-2026-10-07/task-level-report-reproduced.json
```

Outputs cannot be overwritten. The report revalidates corpus/profile/adapter/
controller identities, raw protocol frames, observations, and decisions before
joining human reviews. Overlapping supported profiles are rejected to prevent
selecting a favorable rerun. Original payloads, notes, and execution artifacts
remain local under ignored `data/`; redistribution rights are not established.
Rebuilt image IDs can differ; every new run records its actual identity.

The separate `pytest-marks-posthoc.json` and `sympy-filename-posthoc.json` profiles
and corresponding adapters can be run with the pytest and scientific images,
respectively. Both must remain outside the primary manifest. Their local outputs
are `pytest-marks-posthoc-execution.json` and `sympy-filename-posthoc-execution.json`.

## Limits and next evaluation work

These task probes do not establish full issue coverage or whole-patch
correctness. The original agent-visible test inventory is unknown, and this
expanded experiment does not run each repository's complete suite. It therefore
does not claim these patches passed all visible or repository tests. Static-only
and repository-only comparative accuracy also await compatible evidence and
human labels. Observations share a process with candidate code and retained
JSON is unsigned; hashes detect stale inputs, not malicious evidence forgery.

The next work is human adjudication, especially the four findings above;
reliable environments for currently abstained cases; and frozen repository-test
baselines. After that, compare evaluators on identical labeled cohorts, publish
coverage beside conditional accuracy, and reserve new tasks and related patch
families for held-out evaluation.
