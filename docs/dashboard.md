# Run a verification from the dashboard

Start the API, frontend, and separate worker as described in
[execution-jobs.md](execution-jobs.md). Configure the trusted image ID before
starting the API. Sign in with GitHub; API restarts currently require signing in
again because sessions are in memory.

1. Choose **New verification**, enter a public GitHub PR URL and task description,
   and create a saved draft.
2. Select **Capture PR**. CodeHound records an immutable baseline (merge base),
   candidate SHA, diff hash, and changed files. A new PR revision needs a new draft.
3. Read the selected **independent test profile's coverage**. A repository needs an
   operator-owned profile; tests cannot be uploaded or selected as filesystem
   paths through the browser. The worker must be online and an image configured.
4. Select **Run verification**. The dashboard polls progress while the worker
   runs the same configured checks on both revisions. **Cancel execution** requests
   cleanup; wait for the cancelled state before starting another run.
5. Inspect **Execution evidence**. Improvements are fail→pass; regressions are
   pass→fail; failures on both revisions remain unresolved. Missing or invalid
   evidence is inconclusive. A completed job is not necessarily a passing patch.
6. Expand raw evidence, identity, changed files, or integrity hints as needed.
   **Export execution** includes that run's saved artifact; **Export report**
   includes the PR snapshot and the latest execution summary. Both are JSON.

Execution history shows the latest 20 jobs. The verification checks section always
reflects the latest execution, even when inspecting an older artifact. Full task
completion and requirement adherence remain unverified; no numerical confidence
score is invented. Filename-based test-change hints require review.

## First real run

Use a CodeHound PR such as
[the comparison-engine PR](https://github.com/shuklashreyas/CodeHound/pull/1).
The bundled profile exercises two visible and six independent URL-parser cases on
both revisions. For this PR, unchanged passing cases are expected: the patch does
not change the URL parser. This is a plumbing/dogfooding check, not verification of
the comparison engine. These public cases are not secret benchmark tests.

For a reproducible improvement, overfit fix, and real regression, use the local
pagination experiment in [independent-evaluator.md](independent-evaluator.md).

## Recovery

- **No profile:** the operator must configure one for this repository.
- **Image not configured / worker offline:** correct backend setup, then use
  **Refresh status**.
- **Capture failed:** read the reason and retry capture. Completed snapshots stay
  immutable.
- **Request interrupted:** refresh evidence before retrying. Queue submissions
  reuse an idempotency key for an uncertain retry within the mounted report.
- **Execution failed:** inspect its reason; a new run preserves the earlier record.
- **Sign-in expired:** reconnect GitHub. Saved records survive sign-out.

The sample report remains illustrative and never supplies evidence to a saved run.
