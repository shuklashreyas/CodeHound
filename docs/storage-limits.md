# Bounded admission of retained verification and execution records

CodeHound counts saved verifications and execution history before admitting new
records. Every status counts, including drafts, failed verifications, completed
jobs, failed jobs, and cancelled jobs. Queue capacity and request windows are
separate controls: finishing or cancelling a job releases pending queue capacity
but retains its history record.

| Environment setting | Default | Allowed values |
| --- | --- | --- |
| `CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT` | 1,000 | 1–1,000,000 records |
| `CODEHOUND_MAX_VERIFICATIONS_TOTAL` | 50,000 | 1–1,000,000 records |
| `CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT` | 1,000 | 1–1,000,000 records |
| `CODEHOUND_MAX_EXECUTIONS_TOTAL` | 50,000 | 1–1,000,000 records |

The supplied Compose deployment forwards these settings from the shell or its
environment file to the API. Unset settings retain the application defaults;
explicitly empty settings reach validation as empty values.

All API instances sharing the database should use the same settings. Values must
be decimal integers without signs, whitespace, or leading zeros. Invalid settings
fail startup validation, and attempted new admission fails closed with HTTP 503.

Owner and global counts are checked in the insertion transaction. PostgreSQL uses
transaction-scoped advisory admission locks; SQLite uses an immediate write
transaction. Concurrent admissions cannot exceed the configured count when all
writers use these stores and settings. Existing databases may already contain
more records than a newly lowered limit; their contents are preserved and new
admission remains blocked until the operator resolves the capacity condition.

A full retained-record limit returns HTTP 409 with a message requiring operator
action. It has no `Retry-After` timer because these quotas do not reset with time.
The operator must deliberately adjust the configured limit or retained records
before retrying. There is no automatic deletion, expiration of history, or new
retention-management API in this change.

An idempotent retry that matches an existing record is returned before the retained
record quota check, even at capacity. Reusing the same key with different inputs
still returns a conflict. Ordinary request rate limits continue to charge retries.
Existing records remain readable and exportable. An existing ready verification
can run while its verification quota is full, provided the execution history and
pending queue budgets have capacity. Intake and cancellation remain available.

These are **logical record-count admission limits**, not byte limits. A single
snapshot or execution artifact can consume substantially more storage than another.
These controls do not impose a filesystem quota, account for database pages or
transaction logs, or bound Git checkout disk consumption. Deployments still need
storage monitoring, deliberate retention policies, and filesystem/database capacity
controls appropriate to their artifacts.

`pytest tests/test_storage_limits.py` checks strict settings, idempotency, readable
history at capacity, terminal job states, API errors, and concurrent SQLite
admission. Set `CODEHOUND_TEST_POSTGRES_URL` to run PostgreSQL integration checks;
each check owns and removes a separate random schema.
