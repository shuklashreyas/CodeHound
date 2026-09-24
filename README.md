# CodeHound

**An independent verification layer for AI-generated code.**

Can an automated verification system detect incorrect AI-generated patches that
still pass their visible tests? CodeHound investigates this through independent
execution, hidden tests, patch integrity checks, and repository context.

## What works today

- **GitHub sign-in:** browse public repositories and select an open PR.
- **Dashboard:** all 13 planned verification dimensions, clearly labeled sample
  evidence, filters, changed files, requirements, and report export.
- **Saved drafts:** signed-in submissions are stored by GitHub account and restored
  on refresh. Signed-out drafts remain session-only. The UI loads the latest 100.
- **PR intake API:** capture immutable commit references, a diff and its SHA-256,
  file inventory, PR description, and integrity review hints.
- **Persistence:** PostgreSQL in Docker Compose; SQLite locally without Docker.
  Versioned Alembic migrations run at API startup.
- **Restricted Python execution:** a standalone Docker runner with CPU, memory,
  process, time, and output limits, plus independent read-only tests.
- **Execution jobs:** persistent queue, progress, cancellation, worker leases,
  claim-aware crash recovery, and evidence exports.
- **Independent JSON evaluator:** expected answers and assertions stay outside candidate
  containers; supports operator-defined Python function profiles.
- **Structural test review:** inspect removed tests, changed assertions and new skip
  markers in changed Python test files without importing candidate code.
- **Requirement evidence:** operator-defined requirements mapped to before/after
  test outcomes, with missing coverage kept explicit.
- **Python impact:** differential syntax checks and bounded static import trails
  identifying possible downstream files.
- **Benchmark runner:** a labeled 12-patch synthetic corpus, visible-only vs independent
  decisions, false-positive counts, split-aware metrics, and atomic evidence checkpoints.
- **Reproducible fixture:** a correct pagination fix passes both suites; an overfit
  fix passes visible tests but fails independent cases.

The signed-in dashboard connects the full flow: **capture PR → select an operator
profile → run both revisions → compare evidence**. It shows live progress,
cancellation, execution history, changed files, per-case outcomes, and JSON exports.
The separate worker executes code; the web/API process queues jobs. Repositories
without an operator-owned profile cannot run yet. The bundled CodeHound profiles
check PR URL parsing and comparison-verdict aggregation, not entire PR correctness.
A `ready` intake record means evidence was captured, not that the patch is correct.
Unexecuted checks remain `not_run`, and confidence is unscored.

## Project structure

```text
backend/
  src/codehound/
    api/             Authentication, repositories, verifications, health
    core/            Request limits and execution resource identity
    db/              Models, transactions, and schema migrations
    repositories/    URL validation, bounded GitHub intake, disposable Git checkouts
    evaluation/      Operator profiles, requirements, report contracts
    execution/       Docker execution, comparison, source inspection, worker
    benchmark/       Labeled experiment manifests, runner, and metrics
  tests/             Unit and integration tests
  fixtures/          Pagination, refresh-token, and interval benchmark examples
  test-environments/ Trusted execution image definitions
frontend/
  src/               React dashboard and GitHub connection views
  public/            CodeHound logo
data/                Ignored local database and execution evidence
docs/                Architecture and API notes
compose.yaml         Local PostgreSQL and API services
```

## Run locally

Requirements: Node.js 22.12+ (Node 24 recommended), Python 3.11+, and Git.
Docker is needed for Compose and candidate-code execution, but not for local API
metadata intake or SQLite persistence.

From the repository root:

```sh
cp .env.example .env  # First setup only; preserve existing credentials.
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn codehound.main:app --app-dir src --reload --env-file ../.env --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open [CodeHound](http://localhost:5173), [API documentation](http://localhost:8000/docs),
[process health](http://localhost:8000/api/health), or
[database readiness](http://localhost:8000/api/health/ready).
Vite forwards `/api` to the backend. Use `localhost` consistently for OAuth cookies.
Access logging is disabled in the example to avoid recording OAuth callback codes.

Without database settings, local source checkouts use `data/codehound.db`. Set
`CODEHOUND_DATABASE_URL` to a SQLAlchemy `postgresql+psycopg://...` URL to use PostgreSQL
outside Docker. URL-encode special characters in credentials when constructing a URL.
The database contains issue text and evidence; keep `data/` out of Git.

### Docker Compose

```sh
docker compose up --build -d
```

Compose provisions PostgreSQL, waits for database health, then starts the API and
applies migrations. Its separate credential settings support passwords containing
URL punctuation. Run the frontend separately using the commands above.

`docker compose down` stops services and keeps database data. Adding `--volumes`
also deletes the local database. Containers bind their exposed ports to localhost.
The API container is **not** the environment used to execute candidate patches.

## GitHub sign-in setup

1. Register an **OAuth app** in [GitHub Developer settings](https://github.com/settings/developers).
2. Set the homepage to `http://localhost:5173`.
3. Set the callback to `http://localhost:5173/api/auth/github/callback`.
4. Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in your local `.env`.
   Never place the secret in a `VITE_` variable or commit it.
5. Restart the backend, then select **Sign in with GitHub**.

If you change the origin, update `CODEHOUND_FRONTEND_URL` and the OAuth callback
together. No additional OAuth scopes are requested: this version reads public
profile/repository information and excludes private repositories.

State and PKCE protect sign-in. Access tokens stay on the server; the browser gets
an opaque HttpOnly cookie. HTTPS origins use Secure cookies. Sign-out removes the
local session; revoke the underlying grant in [GitHub Applications](https://github.com/settings/applications).

**Sessions are currently process-local:** restarting the API signs users out, but
saved verification records remain. Run one API worker. A deployment needs a shared,
expiring credential/session store, HTTPS, and request rate limits. Accounts are
identified by immutable GitHub user IDs rather than changeable login names.

## Capture PR evidence

Signed-in users can create drafts in the UI. The API provides:

| Endpoint | Behavior |
| --- | --- |
| `POST /api/verifications` | Save `pr_url` and `issue_text`; optional UUID `Idempotency-Key` |
| `GET /api/verifications?limit=30&offset=0` | List the current account's records |
| `GET /api/verifications/{id}` | Read a record and its evidence |
| `POST /api/verifications/{id}/intake` | Capture an open public PR; retry a failed attempt |
| `GET /api/verifications/{id}/export` | Download the evidence report as JSON |

Writes require the session cookie and `X-CodeHound-Request: 1` from the configured
frontend origin. Other accounts receive 404 for records they do not own. Reusing
an idempotency key with different inputs returns 409. Captured snapshots are
immutable: submit a new draft to inspect an updated PR.

For an anonymous, read-only CLI capture without signing in:

```sh
cd backend
PYTHONPATH=src .venv/bin/python -m codehound.repositories.capture \
  https://github.com/OWNER/REPO/pull/NUMBER --output ../data/pr-evidence.json
```

The CLI refuses to overwrite an existing output file. See [API and evidence details](docs/api.md)
for limits, retry behavior, and the distinction between observations and verdicts.

## Run the independent-test demonstration

Build a trusted test image, then resolve its immutable local ID:

```sh
docker build -t codehound-python-test:dev backend/test-environments/python
CODEHOUND_IMAGE_ID="$(docker image inspect --format '{{.Id}}' codehound-python-test:dev)"
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.demo \
  --image "$CODEHOUND_IMAGE_ID" --output data/pagination-evidence.json
```

The report records stdout, stderr, exit codes, duration, image identity, and limits
for the original code and three candidate fixes. The deliberately overfit patch
passes the issue's example but fails varied cases. This is a public synthetic
fixture, **not** a hidden benchmark or a detection-rate claim.

The runner requires a trusted local image ID, disables networking, runs as a
non-root user, and mounts candidate code, tests, and the harness read-only. Trusted
pytest configuration takes precedence over candidate configuration. Exit codes
remain execution evidence: arbitrary malicious Python can interfere with tests
inside the same process. Strong adversarial isolation requires an external test
protocol and further hardening before production use.

## Compare a captured PR in Docker

Supply tests you control independently of the candidate patch. The command checks
out both pinned revisions, runs the same suites against each, and records the
results and test-suite hashes. It accepts flat Python modules and `src/` layouts.
Required third-party dependencies must already be installed in your trusted image;
CodeHound does not run repository setup scripts or install its dependencies.

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.verify \
  --snapshot data/pr-evidence.json \
  --visible-tests /absolute/path/to/trusted-visible-tests \
  --hidden-tests /absolute/path/to/independent-tests \
  --image-id "$CODEHOUND_IMAGE_ID" \
  --output data/comparison-evidence.json
```

`--hidden-tests` is optional. The command refuses to overwrite evidence files.
Comparisons match individual test IDs and report improvements, regressions,
unresolved failures, missing tests, and unverified checks. Verdicts are
`candidate_improves`, `regression_detected`, `incomplete`,
`no_behavior_change_observed`, or `inconclusive`. Timeouts, infrastructure errors,
and absent reports cannot count as improvements. Skips and expected failures are
not treated as passes. See [comparison semantics](docs/comparison-engine.md). A test improvement is not a correctness verdict.
This command is operator-controlled and is not exposed through the web API.

For stronger assertion isolation, use [independent JSON evaluation](docs/independent-evaluator.md)
with `--mode independent` and operator-owned JSON profiles. This mode judges
observed values outside candidate containers and does not mount expected answers.

For saved PR runs, follow [execution worker setup and API](docs/execution-jobs.md).
Only repositories with a matching operator-owned profile can be evaluated.

## Development checks

```sh
backend/.venv/bin/ruff check backend
backend/.venv/bin/ruff format --check backend
backend/.venv/bin/python -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
```

The default tests isolate storage in temporary SQLite databases. Set
`CODEHOUND_TEST_POSTGRES_URL` to a **dedicated test database** to enable PostgreSQL
integration checks. Set `CODEHOUND_TEST_IMAGE_ID` to the trusted Docker image ID to
enable actual container tests. CI runs PostgreSQL and Docker integration jobs as
well as frontend tests and the production build.

See [architecture and remaining boundaries](docs/architecture.md) and the
[dashboard walkthrough](docs/dashboard.md). Next: broaden independently retained
test profiles and gather human-reviewed real agent patches. The
[benchmark runner](docs/benchmark.md) provides reproducible experiment plumbing; its
public synthetic corpus does not establish real-world detection performance.

Further implementation details: [worker recovery](docs/worker-recovery.md),
[requirement evidence](docs/requirement-evidence.md), and
[Python syntax/import impact](docs/python-impact.md).
