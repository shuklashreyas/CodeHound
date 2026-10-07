# Persistent verification jobs

The API queues work; a separate worker performs checkouts and Docker execution.
The API never accepts shell commands, image names, filesystem paths, or test
expectations from a browser. Callers select an operator-owned profile by ID, and
that profile must match the submitted repository.

## Local setup

1. Build the trusted image using the root README's Docker command.
2. Put its immutable ID in `CODEHOUND_EXECUTION_IMAGE_ID` in your local `.env`.
3. Start the API with `--env-file .env` (adjust the relative path to your directory).
4. In another terminal, start the worker against the **same database**:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.worker --env-file .env
```

`--once` processes at most one queued job and exits. SIGINT/SIGTERM cancel current
execution with cleanup before shutdown. Worker presence is visible to clients;
queued work remains saved when no worker is online. PostgreSQL migrations use an
advisory lock and SQLite startup obtains a write lock before migrating, so API and
worker startup cannot race to create the schema.

For a Compose API, configure the host worker's `CODEHOUND_DATABASE_URL` to the same
PostgreSQL database through localhost. The API container does not receive the
Docker socket. Do not run untrusted workloads on a shared production API host.

## Profiles

The bundled profiles are public dogfooding fixtures, not secret benchmark tests or
full coverage of a CodeHound PR:

| Profile | Coverage | Visible / independent cases |
| --- | --- | --- |
| `codehound-url-contract` | Public PR URL parsing and unsafe URL rejection | 2 / 6 |
| `codehound-verdict-contract` | Verdict aggregation, regression priority, uncertainty, and improvement signals | 3 / 9 |

The verdict profile requires `codehound.execution.results.summarize_comparisons`
in both revisions. For CodeHound PR #1, that module was newly added, so the baseline
cannot satisfy the adapter contract and the comparison remains inconclusive. Use
later PRs to check its existing behavior. Missing targets are never silently treated
as passing checks. Other repositories have no profile until an operator provides one.

Set `CODEHOUND_PROFILE_DIR` to replace the bundled profiles with JSON files you
control. Each file contains `id`, `label`, `repository`, `coverage`, a `visible`
trusted suite, and an optional `hidden` trusted suite. Suite format is documented
in [independent-evaluator.md](independent-evaluator.md). The API freezes the full
profile, image ID, and commit/diff identities when a job is queued. Profile bodies
and expected answers are never returned by API serializers. Optional requirement
mappings are described in [requirement-evidence.md](requirement-evidence.md).

Profiles can optionally set `execution_image_id` to an immutable local image ID
in the form `sha256:` followed by 64 lowercase hexadecimal characters. This
operator-owned pin takes precedence over `CODEHOUND_EXECUTION_IMAGE_ID`; profiles
without a pin (or with JSON `null`) retain that global fallback. Empty strings,
image tags, registry URLs, and malformed IDs fail profile validation. Operators
can therefore select different prepared Python/dependency environments for
different profiles without changing a global setting. Candidate repositories and
API request bodies cannot supply image IDs or build instructions.

Each profile availability response includes `execution_configured` and the
resolved `execution_image_id`. These describe current operator configuration;
they do not establish that the image exists on the worker's Docker daemon. The
worker checks local image presence when it starts the job. The browser enables
execution for the selected profile only. The older top-level `configured` flag
remains true when the global image is configured or a compatible profile has a
pin, and must not be used to infer availability of every profile.

The queued job's stored image ID and profile snapshot remain authoritative after
operator files or environment settings change. Historical profile summaries stay
independent of current availability; the job's separate `image_id` records the
environment selected for that execution. Pre-existing jobs and profiles without
the optional field remain valid. No candidate builds or dependency installation
are added by this setting.

## API

All routes require a GitHub session; writes also require `X-CodeHound-Request: 1`
and a valid origin.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/verifications/{id}/profiles` | Compatible profile summaries, configuration, worker presence |
| `POST /api/verifications/{id}/executions` | Queue `{"profile_id":"…"}` after successful PR intake |
| `GET /api/verifications/{id}/executions` | Most recent 20 execution summaries |
| `GET /api/executions/{id}` | State, coverage, pinned revisions, full evidence |
| `POST /api/executions/{id}/cancel` | Cancel queued work or request cancellation of running work |
| `GET /api/executions/{id}/export` | Download execution metadata and evidence |

Queueing returns 202 and a Location header. An optional UUID `Idempotency-Key`
allows safe retries; an identical repeat returns 200 and the original job. At most
one job may be queued/running per verification. Admission is also bounded to 5
queued/running jobs per account and 50 total by default. A full queue returns 429
with `Retry-After: 30`; an idempotent retry still returns its existing job.
`CODEHOUND_MAX_PENDING_PER_ACCOUNT` (1–100) and
`CODEHOUND_MAX_PENDING_EXECUTIONS` (1–1000) configure these limits. Admission is
serialized within a short database transaction, so concurrent requests cannot
exceed the limits. Completed/cancelled/expired jobs release capacity. These are
backlog bounds, not request-rate or disk quotas. Another account receives 404 for
all detail, list, cancel, profile, and export routes.

Jobs move through `queued → running → completed/failed/cancelled`. `completed`
means an evidence artifact was stored, not that the patch passed or every stage finished.
Its assessment can still
be incomplete, inconclusive, or a regression. Setup failures preserve a sanitized
reason. Completed evidence is immutable; a retry creates another job.

Workers claim jobs atomically and renew a 90-second lease. Expired claims become
failed on the next worker poll, release the active-job slot, and cannot be
resurrected by late results. Cancellation wins over a concurrent completion and
clears any candidate artifact. Running cancellation is checked every five seconds;
Docker cleanup finishes before the worker records cancellation. Each evaluation
has a 560-second work budget within a 600-second hard deadline, in addition to suite
and case limits. Independent suites run before optional inspections. Exhausted
optional stages become inconclusive while completed independent evidence is saved;
unfinished independent cases remain `not_run`. See [deadline handling](execution-deadlines.md).

Verification detail/export now includes the latest execution summary. Configured
visible/independent checks can have actual pass/fail/inconclusive outcomes. Task
completion and other unimplemented checks remain unrun. Operator requirement
evidence and Python source inspection have their own limited coverage; neither
establishes semantic issue completion. Absence of detected regressions is not a
global safety guarantee.

## Operational limits

Default sessions are process-local; use one API process or configure
[encrypted shared sessions](shared-sessions.md). The queue can use multiple
execution workers, each processing one job at a time. [Request limits](request-limits.md)
bound login and mutation admission. [Retained-record limits](storage-limits.md)
bound saved history independently of pending queue capacity. Dedicated disk quotas and deployment hardening
are still required before public
production use. Claim-aware [orphan recovery](worker-recovery.md) now handles aged
resources owned by expired jobs; it is not a storage quota.
