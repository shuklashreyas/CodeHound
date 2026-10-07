# AI-patch development pilot

This phase evaluates published agent-generated patches. It does not extend the
MVP product UI or treat its public synthetic demonstrations as research results.
See the [first pilot report](pilot-2026-10-07.md) for actual collection coverage and
a helper-profile rejection that conflicts with improved public API behavior.

The first cohort targets 50 nonempty predictions from a single published
mini-SWE-agent submission on SWE-bench Verified. Sampling takes one instance at a
time from each repository in sorted order, using sorted instance IDs within each
repository. Selection must not consult upstream resolved lists, evaluation logs,
human verdicts, or CodeHound decisions. Empty predictions and missing task matches
are accounted for separately. This is a repository-balanced convenience sample,
not the natural task distribution or a representative sample of all coding agents.

The corpus, original downloaded payloads, execution artifacts, and review packets
live under ignored `data/`. Source specifications and tooling are versioned here.
Retain the exact source revisions, original byte hashes, transformation hashes,
task/base identities, issue text and patch bytes when reproducing the cohort.

Follow [the pinned source recipe](sources/README.md) to reproduce the 50-case
cohort across 12 repositories. The source run contains 441 nonempty predictions
for 500 Verified tasks; 59 tasks have no published prediction. Sampling is limited
to those 441 available predictions, not all 500 tasks. Each retained case includes
its issue, baseline commit, patch, producer attribution, and source-bound identity.

## Freeze checks before examining results

`profiles/requests-1921.json` was authored from the issue statement and the pinned
baseline implementation of `requests.sessions.merge_setting`, before the profile
author inspected the agent patch or its execution results. It checks removing
`None` values inherited from session headers while preserving merges and explicit
request overrides. The configuration binds the repository, base commit and exact
issue bytes. It contains one issue example and ten independent behavioral probes.

These are newly authored operator probes. We do not know whether these exact cases
were visible to the original coding agent. The runner's `visible_only` key means
the operator's issue-example baseline, not a verified inventory of agent-visible
tests. The profile observes a helper contract, not the complete HTTP request path
or every requirement of the issue.

The historical Requests baseline needs Python 3.9 for its legacy `collections`
imports. `environments/requests-python39/Dockerfile` prepares that isolated test
runtime with pinned pytest and Ruff; it is not the API deployment environment.
Candidate execution remains offline and resource-limited. Record the resolved
immutable image ID for every run.

No repository-test selection is configured for this first profile: the baseline
has one broad test file with network-dependent cases. Repository-only evidence
must therefore abstain. Missing evidence is not a passing test suite. Differential
Ruff still uses its existing Python 3.11 analysis target, which is a limitation
when interpreting this historic code.

## Human judgments are a separate input

SWE-bench Verified's human filtering concerns tasks and their tests; it does not
establish the correctness of every generated patch. Neither upstream automated
outcomes nor CodeHound's own decisions may supply human ground truth.

Review packets omit producer metadata and evaluator outcomes. A reviewer should
read the task, original code and patch, inspect relevant surrounding behavior,
and record concrete evidence for `valid`, `invalid`, or `uncertain`. A valid patch
must satisfy the task without breaking required existing behavior; matching the
upstream reference patch is not required. Incomplete fixes, regressions, task
mismatches and weakened tests should be explained individually.

Names and timestamps are recorded as reviewer attestations, not authenticated
proof that a human performed the work. Issue or patch contents may themselves
reveal their origin; packet generation cannot guarantee anonymity. Contradictory,
uncertain, missing and stale reviews cannot become accuracy labels. Use a second
independent reviewer and explicit adjudication before treating a larger cohort as
a publishable correctness benchmark.

Until human review is complete, report collection and execution coverage, case
observations and abstentions. Detection and false-positive rates without eligible
human labels must be `null`. Public tasks used to develop these checks remain a
development pilot; future held-out tasks and related patch families must remain
separate from evaluator development.

## Run the pilot

After importing the corpus, build the trusted historical Requests environment.
The runner accepts an immutable local image ID, never an image tag. It applies
bounded text patches to disposable copies, executes candidate code only inside
restricted containers, and never runs repository installation hooks on the host.

```bash
docker build -t codehound-requests-benchmark:pilot benchmarks/environments/requests-python39
CODEHOUND_PILOT_IMAGE=$(docker image inspect --format '{{.Id}}' codehound-requests-benchmark:pilot)
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.corpus_run \
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --mapping benchmarks/profiles/requests-1921.json \
  --image-id "$CODEHOUND_PILOT_IMAGE" \
  --max-seconds 600 \
  --output data/ai-patch-pilot-2026-10-07/execution.json
```

The first mapping supports one task. All other corpus rows remain explicit
unsupported cases; they are not silently dropped from the experiment. The runner
does not accept review labels. It retains baseline/candidate observations, profile
and evaluator identities, workspace hashes, static findings, and partial evidence
when interrupted. Output files cannot be overwritten by a new invocation.

Generate review packets separately:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.review packet \
  data/ai-patch-pilot-2026-10-07/corpus.json data/ai-patch-review-2026-10-07
```

The generated template is intentionally incomplete. A human must fill in each
completed review, including a reason and personal attestation; leave unfinished
cases out of the submitted collection. An empty collection is valid for reporting
coverage while reviews are pending:

```json
{"schema_version": 1, "reviews": []}
```

Save that collection as `data/ai-patch-pilot-2026-10-07/reviews.json`, then score
retained evidence without re-running candidate code:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.benchmark.corpus_metrics \
  --corpus data/ai-patch-pilot-2026-10-07/corpus.json \
  --mapping benchmarks/profiles/requests-1921.json \
  --evidence data/ai-patch-pilot-2026-10-07/execution.json \
  --reviews data/ai-patch-pilot-2026-10-07/reviews.json \
  --output data/ai-patch-pilot-2026-10-07/metrics.json
```

The report compares operator issue probes, independent probes, frozen repository
tests when configured, and static screening. It separates probe-passing and
repository-test-passing subsets, retains abstentions in reviewed denominators,
and excludes unknown or conflicting human verdicts from accuracy calculations.
Source identities detect accidental stale or mismatched evidence; retained JSON
is operator-controlled evidence, not a cryptographic attestation of execution.

Sources: [SWE-bench experiments](https://github.com/SWE-bench/experiments),
[Verified task documentation](https://www.swebench.com/verified), and
[Verified task data](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified).
