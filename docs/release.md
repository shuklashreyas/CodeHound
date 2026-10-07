# MVP release checklist

The planned first MVP tag is `v0.1.0-mvp.1`. This checklist does not establish
that the tag exists or that a particular commit has passed CI.

The MVP supports public PR intake, saved account-owned reports, operator-owned
Python profiles, a durable execution worker, independent JSON assertions, frozen
baseline repository tests, requirement evidence, and bounded source analysis.
Passing checks establish the configured contract only; complete PR correctness
and calibrated confidence remain unverified. See [architecture](architecture.md)
and [evaluator provenance](evaluator-provenance.md) for the current boundaries.

## Validate the main commit

Use a clean checkout or worktree and run all commands from its repository root.
The cleanliness check must succeed before switching branches. Preserve existing
credentials; these checks do not require copying or sourcing a local `.env`.

```sh
test -z "$(git status --porcelain)"
git fetch origin
git switch main
git pull --ff-only origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
CODEHOUND_RELEASE_COMMIT=$(git rev-parse HEAD)

python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e './backend[dev]'
npm --prefix frontend ci

backend/.venv/bin/ruff check backend
backend/.venv/bin/ruff format --check backend
backend/.venv/bin/python -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
```

Run each command only after the previous command succeeds. Requirements are in
the [root README](../README.md). Default backend tests use isolated temporary
SQLite databases; PostgreSQL, actual Docker execution, and public repository
checks require the explicit settings below.

Build the operator-controlled image and resolve its immutable local ID:

```sh
docker build -t codehound-python-test:dev backend/test-environments/python
CODEHOUND_IMAGE_ID=$(docker image inspect --format '{{.Id}}' codehound-python-test:dev)
```

Set `CODEHOUND_TEST_POSTGRES_URL` to a **dedicated, disposable test database**
using a `postgresql+psycopg://...` SQLAlchemy URL. Never point it at the application
database or a database with records to preserve. Then run the complete backend
suite with all integration checks enabled:

```sh
: "${CODEHOUND_TEST_POSTGRES_URL:?Set a dedicated disposable PostgreSQL test database URL}"
CODEHOUND_TEST_POSTGRES_URL="$CODEHOUND_TEST_POSTGRES_URL" \
CODEHOUND_TEST_IMAGE_ID="$CODEHOUND_IMAGE_ID" \
CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS=1 \
backend/.venv/bin/python -m pytest backend/tests
```

This includes public GitHub intake, the durable job queue, actual Docker
execution, persisted evidence, and owner-scoped exports. The live API flow uses
TestClient and synthetic authentication in an isolated database; it does not
exercise browser GitHub OAuth. Follow the [dashboard walkthrough](dashboard.md)
separately when checking browser sign-in.

## Retain a fresh demonstration

Choose new evidence paths for every run; these CLIs refuse existing output files.
The timestamp suffix below separates repeat runs. Keep the complete artifacts
with the tested main commit and image ID in the release record.

```sh
CODEHOUND_RELEASE_RUN=$(date -u +%Y%m%dT%H%M%SZ)
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.real_repository \
  --snapshot backend/fixtures/real_repository/packaging-pr-925/snapshot.json \
  --profile packaging-name-validation \
  --image-id "$CODEHOUND_IMAGE_ID" \
  --output "data/packaging-main-$CODEHOUND_RELEASE_RUN.json"

PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.real_repository_experiment \
  --snapshot backend/fixtures/real_repository/packaging-pr-925/snapshot.json \
  --mutations backend/fixtures/real_repository/packaging-pr-925/mutations \
  --image-id "$CODEHOUND_IMAGE_ID" \
  --output "data/packaging-adversarial-main-$CODEHOUND_RELEASE_RUN.json"
```

Confirm the real upstream candidate has three independent improvements and no
observed regressions. The experiment's outer status must be `completed`: its
upstream control passes all 3 visible and 13 independent cases; both deliberately
bad mutations pass all visible cases and 54 frozen repository tests, while the
independent evaluator rejects both (two failures for overfit, one for regressive).
Repository test evidence must remain separate from the independent assessment.
See [the reproduction](real-repository.md) and [the experiment](real-experiment.md).

These two negative patches are operator-authored demonstrations. They provide no
accuracy estimate for real AI-generated patches; that requires human-reviewed
ground truth and held-out evaluation tasks.

## Tag the validated commit

After the tests and demonstrations succeed, confirm the checkout is still clean
and unchanged. Inspect CI for the exact recorded main commit:

```sh
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = "$CODEHOUND_RELEASE_COMMIT"
git fetch origin main
test "$(git rev-parse origin/main)" = "$CODEHOUND_RELEASE_COMMIT"
```

Use the repository's [CI runs](https://github.com/shuklashreyas/CodeHound/actions/workflows/ci.yml)
to inspect that commit, or use the optional GitHub CLI if installed:

```sh
gh run list --repo shuklashreyas/CodeHound --workflow CI --event push \
  --branch main --commit "$CODEHOUND_RELEASE_COMMIT" \
  --json headSha,status,conclusion,url
```

Require a completed, successful CI run for that exact commit. Missing, pending,
cancelled, or failed runs are not green CI. If main advances, validate the new
commit before tagging. Only after these conditions hold, create and publish an
annotated tag; an existing tag must never be replaced:

```sh
git tag -a v0.1.0-mvp.1 "$CODEHOUND_RELEASE_COMMIT" \
  -m 'CodeHound MVP: independent verification and reproducible adversarial demonstration'
git push origin refs/tags/v0.1.0-mvp.1
```
