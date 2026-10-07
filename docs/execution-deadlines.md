# Execution deadlines and retained evidence

Queued worker executions have a 560-second work budget and a 600-second hard
deadline. The remaining 40 seconds allow cancellation cleanup and workspace
removal. Independent suites run first, followed by test-integrity inspection,
Python-impact inspection, static analysis, and configured frozen repository tests.

Each independent suite keeps its operator-configured deadline, capped by the
remaining shared work budget minus a 20-second container-cleanup reserve. Once
that reserve is reached, no new case containers start. Every expected case still
appears in both revision reports: completed observations retain their outcomes,
and unfinished cases are `not_run`. Missing observations cannot produce a
supported requirement or an improvement assessment; observed regressions remain
visible even when other cases are unverified.

Optional stages receive the remaining shared work budget. If a stage exhausts it,
that stage returns explicit `inconclusive` evidence and later optional stages are
skipped with the same reason. Completed independent evidence is persisted. The
artifact's `execution_budget` records `status` (`completed` or `exhausted`),
`work_timeout_seconds`, `suite_cleanup_reserve_seconds`, and `incomplete_stages`.
An execution job marked completed means its artifact was stored; the individual
evidence statuses describe which checks completed.

Only a deadline timer's own expiration is converted into incomplete evidence.
An internal evaluator `TimeoutError`, worker shutdown, user cancellation, or lost
execution lease still follows the worker failure/cancellation path. Cancellation
never publishes a partially completed artifact. The 600-second guard remains a
fail-safe for abnormal infrastructure or cleanup stalls; if that guard expires,
the job fails rather than claiming that cleanup or evaluation completed.

Standalone `verify_snapshot` and command-line callers without the worker budget
retain their original stage order and configured suite limits. They do not
receive `execution_budget` metadata unless explicitly run under that context.
