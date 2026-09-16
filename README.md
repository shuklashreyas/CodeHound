# CodeHound

**An independent verification layer for AI-generated code.**

Can an automated verification system detect incorrect AI-generated patches that
still pass their visible tests? CodeHound investigates this through independent
test execution, hidden tests, patch integrity checks, static analysis, and
repository impact analysis, producing evidence-backed verification reports.

## Current status

This repository is an initial infrastructure scaffold:

- FastAPI backend with a typed liveness endpoint and an API smoke test.
- React + TypeScript frontend with a live API connection indicator.
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
uvicorn codehound.main:app --reload --host 127.0.0.1 --port 8000
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
