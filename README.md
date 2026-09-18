# CodeHound

**An independent verification layer for AI-generated code.**

Can an automated verification system detect incorrect AI-generated patches that
still pass their visible tests? CodeHound investigates this through independent
test execution, hidden tests, patch integrity checks, static analysis, and
repository impact analysis, producing evidence-backed verification reports.

## Current status

This repository is an initial infrastructure scaffold:

- FastAPI backend with a typed liveness endpoint and an API smoke test.
- React + TypeScript verification dashboard using the CodeHound logo.
- Docker Compose services for the API and a persistent PostgreSQL database.
- Python linting, frontend type checking, and GitHub Actions CI.
- Separate packages for future repository analysis and evaluation orchestration.

Patch submission, sandboxed evaluation, database models, hidden tests, and ML
are not implemented yet. API health checks process liveness only.

## Project structure

```text
backend/
  src/codehound/
    api/             HTTP routes
    core/            Shared configuration and domain types (planned)
    evaluation/      Verification orchestration (planned)
    repositories/    Repository and patch operations (planned)
    main.py          FastAPI application
  tests/             Backend tests
  Dockerfile
  pyproject.toml
frontend/
  src/               React application and styles
  package.json
data/                Local artifacts (contents ignored by Git)
docs/
  architecture.md    System boundaries and implementation milestones
compose.yaml         Local API and PostgreSQL services
```

## Quick start

Requirements: Docker with Compose, Node.js 22.12+ (Node 24 recommended), and
Python 3.11+ if running the backend outside Docker.

From the repository root:

```sh
cp .env.example .env
docker compose up --build -d
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open [the frontend](http://localhost:5173), [API documentation](http://localhost:8000/docs),
or [API health](http://localhost:8000/api/health).
Vite forwards `/api` requests to the backend during development.

PostgreSQL is available on `127.0.0.1:5432`; local credentials are configured in
`.env`. These defaults are for development. The API does not connect to the database
yet. Database data persists in the `postgres_data` Docker volume.

Stop services with `docker compose down`. Adding `--volumes` also deletes local
database data. The frontend runs separately and stops with Ctrl+C.

## Backend without Docker

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn codehound.main:app --app-dir src --reload --host 127.0.0.1 --port 8000
```

The current health endpoint does not require PostgreSQL. To start only the database,
run `docker compose up -d db` from the repository root.

## Development checks

From `backend/`, with its virtual environment activated:

```sh
ruff check .
ruff format --check .
pytest
```

From `frontend/`:

```sh
npm ci
npm run build
```

GitHub Actions runs these checks on pushes and pull requests. Frontend dependencies
are locked in `package-lock.json`; Python dependencies currently use bounded version
ranges. A deployed frontend will need a same-origin `/api` reverse proxy;
production hosting is not configured by this scaffold.

## Next steps

Start with one reproducible evaluation: repository + original commit + patch →
isolated execution → baseline comparison → saved evidence → verification report.
Then add hidden tests, integrity checks, static analysis, and benchmark baselines.

See [the architecture notes](docs/architecture.md) for isolation requirements and
the research evaluation plan. The development API container is not a sandbox
for executing untrusted candidate patches.

## Frontend preview

The dashboard opens with a clearly labeled illustrative report. It includes all
13 planned check dimensions, expandable evidence, status filters, changed-file
and dependency summaries, execution logs, acceptance criteria, and JSON export.
The repository view supports searching the current session's repositories; Check
coverage explains the planned evaluator dimensions.

Use **New verification** to enter a public GitHub PR URL and task description.
The form validates the URL format and creates an in-memory draft with every check
marked **Not run**. Drafts disappear on refresh. Manual draft submission does not fetch code, execute tests, upload tests, or
persist evaluation records. GitHub sign-in can fetch repository and PR metadata
as described below.
Sample exports are explicitly marked `illustrative_sample`; draft exports are
marked `not_run`. Confidence is intentionally unscored.

The interface supports narrow screens and keyboard interaction. The supplied logo
is stored in `frontend/public/codehound-logo.png`. Fonts load from Google Fonts,
with local sans-serif fallbacks when offline.

## GitHub sign-in and repository access

CodeHound now implements GitHub OAuth sign-in, public repository browsing, and
open-PR selection. The **Sign in with GitHub** button opens the repository screen.
Once connected, select a repository, choose a PR, and review its description in
the verification draft form. Repository and PR lists are paginated; filtering
applies to the current page. This integration reads metadata; it does not clone
repositories or run verification yet.

### Configure a local GitHub OAuth app

1. In [GitHub Developer settings](https://github.com/settings/developers), register
   a **new OAuth app**.
2. Set the homepage URL to `http://localhost:5173`.
3. Set the authorization callback URL to
   `http://localhost:5173/api/auth/github/callback`.
4. Copy `.env.example` to `.env` if you have not already created it. Set
   `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in that local file. Keep the secret
   out of Git and never use a `VITE_` environment variable for it.
5. Restart the API so it reads the settings. With Docker, run
   `docker compose up --build -d api`. Without Docker, run from `backend/`:

   ```sh
   source .venv/bin/activate
   python -m pip install -e '.[dev]'
   uvicorn codehound.main:app --app-dir src --reload --env-file ../.env --host 127.0.0.1 --port 8000
   ```

Use `http://localhost:5173` consistently in your browser. If you change the origin,
update `CODEHOUND_FRONTEND_URL` and the OAuth app callback together. Vite proxies
the `/api` callback and API requests to FastAPI.

The integration requests no additional OAuth scopes: it reads public profile and
repository information. Private repositories remain excluded. Private repository
support should use a GitHub App with explicit repository selection and read-only
permissions instead of requesting the OAuth `repo` scope, which also grants write
access. See [GitHub's OAuth scope documentation](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps).

Authentication uses one-time OAuth state, browser-bound state cookies, PKCE, and
opaque HttpOnly session cookies. Access tokens are kept only on the backend and
are never returned to the frontend. HTTPS origins use Secure cookies. Sign-out
removes the local session; to revoke the GitHub authorization itself, use your
[GitHub authorized applications](https://github.com/settings/applications).

**Development limitation:** sessions and pending OAuth flows are stored in memory
in one API process, expire automatically, and disappear on restart. Use one API
worker for this scaffold. Before deployment, add a shared expiring session store,
encryption for persisted credentials, HTTPS, and appropriate request rate limits.
GitHub authorization cannot be exercised live until you configure your own app;
backend tests cover the flow using mocked GitHub responses.
