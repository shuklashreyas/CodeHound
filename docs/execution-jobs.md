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

The bundled `codehound-url-contract` profile covers **only** CodeHound's public PR
URL parser. It is a public dogfooding fixture, not a secret benchmark or full
coverage of a CodeHound PR. Other repositories have no profile until an operator
provides one.

Set `CODEHOUND_PROFILE_DIR` to replace the bundled profiles with JSON files you
control. Each file contains `id`, `label`, `repository`, `coverage`, a `visible`
trusted suite, and an optional `hidden` trusted suite. Suite format is documented
in [independent-evaluator.md](independent-evaluator.md). The API freezes the full
profile, image ID, and commit/diff identities when a job is queued. Profile bodies
and expected answers are never returned by API serializers.

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
one job may be queued/running per verification. Another account receives 404 for
all detail, list, cancel, profile, and export routes.

Jobs move through `queued → running → completed/failed/cancelled`. `completed`
means the evaluator finished, not that the patch passed. Its assessment can still
be incomplete, inconclusive, or a regression. Setup failures preserve a sanitized
reason. Completed evidence is immutable; a retry creates another job.

Workers claim jobs atomically and renew a 90-second lease. Expired claims become
failed on the next worker poll, release the active-job slot, and cannot be
resurrected by late results. Cancellation wins over a concurrent completion and
clears any candidate artifact. Running cancellation is checked every five seconds;
Docker cleanup finishes before the worker records cancellation. Each evaluation
has a 600-second total deadline in addition to suite and case limits.

Verification detail/export now includes the latest execution summary. Configured
visible/independent checks can have actual pass/fail/inconclusive outcomes. Task
completion, semantic requirement adherence, and other unimplemented checks remain
unrun, and the absence of detected regressions is not a global safety guarantee.

## Operational limits

Sessions remain process-local, so run one API worker. The queue can use multiple
execution workers, each processing one job at a time. Dedicated disk quotas,
request/account quotas, orphan-container/workspace reconciliation, and deployment
hardening are still required before public production use. The local implementation
does not silently claim these operational protections exist.
