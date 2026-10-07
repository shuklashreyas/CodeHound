# AI-patch development pilot

This phase evaluates published agent-generated patches. It does not extend the
MVP product UI or treat its public synthetic demonstrations as research results.

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

Sources: [SWE-bench experiments](https://github.com/SWE-bench/experiments),
[Verified task documentation](https://www.swebench.com/verified), and
[Verified task data](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified).
