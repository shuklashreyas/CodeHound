# Per-test comparison

CodeHound evaluates observable behavior before and after a patch using the same
operator-owned tests. A higher aggregate score is insufficient: one previously
passing behavior can break while several other cases improve.

The Python harness emits a versioned report containing the collected pytest node
IDs, outcomes, durations, bounded failure messages, and collection errors. The
runner checks framing, uniqueness, inventory completeness, and consistency with
the container's exit code. Missing, truncated, contradictory, or duplicate reports
cannot establish an improvement. The execution artifact identifies the immutable
image and hashes the harness/configuration. Both revisions must use the same ones.

| Before | After | Finding |
| --- | --- | --- |
| Failed | Passed | Improvement |
| Passed | Failed | Regression |
| Failed | Failed | Unresolved |
| Passed | Passed | Unchanged pass |
| Skip, xfail, xpass, setup/teardown error | Any | Unverified |
| Any | Skip, xfail, xpass, setup/teardown error | Unverified |

Missing or newly collected test IDs flag collection drift. A successful process
exit without a complete test report is inconclusive, including an early
`os._exit(0)`. Collection errors, timeouts, and infrastructure failures are also
inconclusive. A valid, directly observed pass-to-fail transition remains a
regression even if other cases are unverified; those limitations remain attached.
Without regressions, unverified checks prevent a clean improvement verdict.
Remaining ordinary failures produce `incomplete`, even when other cases improve.
If every case passed on both revisions, the result is `no_behavior_change_observed`:
this is not evidence that the task is complete.

Version 2 comparison artifacts retain the original run logs and add detailed
`test_comparison` data per suite plus an overall `assessment`. Improvements on
visible tests with unresolved independent failures get an explicit signal, not
an accusation of manipulation. Confidence remains unscored.

## Trust boundary

Test files and harness code are mounted read-only from outside the candidate PR.
Candidate pytest configuration cannot select, omit, or redefine the supplied test
suite. The report token prevents accidental confusion with ordinary output; it is
not a secret or a signature.

**This pytest mode still executes candidate Python in the evaluator's process.**
Malicious code can inspect memory, monkeypatch pytest, or forge reports. Structured
validation catches accidental or obvious interference but does not prove tamper
resistance. The evidence explicitly identifies its source as `in_process_pytest`.
The separate [JSON evaluator](independent-evaluator.md) judges candidate outputs
outside Docker for supported function contracts. This stronger boundary applies
only when the report identifies `external_json_assertions`, not to pytest runs.

## Reproducible example

Run the pagination demonstration documented in the root README. It compares the
original against correct, overfit, and regressive candidates. The regressive
candidate improves three independent cases but breaks empty input. Both revisions
have nonzero exit codes; individual test matching still exposes the regression.
