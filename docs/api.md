# Verification API and evidence contract

All verification endpoints require a GitHub session. Records belong to the numeric
GitHub account ID. Signing in under a renamed login does not transfer ownership.
Tokens and session IDs are never stored in verification records or exported.

## Submission

```json
{
  "pr_url": "https://github.com/owner/repository/pull/123",
  "issue_text": "Describe the required behavior and acceptance criteria here."
}
```

`pr_url` must be an HTTPS github.com PR URL with no credentials, custom port, query,
or fragment. Owner/repository names and a positive PR number are validated before
network requests. Canonical URLs remove trailing slashes. Task text must contain
10–20,000 characters. Unknown fields are rejected; request bodies are limited to
128 KiB before JSON parsing. Creating a draft does not verify the PR's existence.

`POST /api/verifications` returns 201 and a Location header. Supply an optional UUID
`Idempotency-Key` to safely repeat a submission: identical inputs return the existing
record with 200; different inputs under that key return 409.

## Intake state machine

```text
draft → intaking → ready
                ↘ failed → intaking
```

`ready` means a consistent evidence bundle was captured, **not** that tests passed.
Ready records are immutable; repeated intake returns the original report without
fetching GitHub again. New revisions require a new submission.

The POST intake request waits for completion, with a 90-second deadline. A database
claim prevents duplicate intake; concurrent requests return 409. A 120-second lease
allows interrupted work to be retried, and old attempts cannot overwrite newer
results. At most 20 attempts are retained. Failed and interrupted attempts are
recorded. A successful HTTP response can contain `status: failed`; callers must
inspect the record state and `failure.code`.

No database connection is held during GitHub requests. A failed attempt retains
its reason without saving a partial bundle. Retrying an expired GitHub session
requires signing in again. Private, unavailable, moved, closed, or overly large PRs
fail explicitly rather than yielding a partial success.

## Evidence bundle

- Public repository identity and PR title/body at capture time.
- `base_target_sha`: the PR target branch revision observed during intake.
- `base_sha`: the **merge base**, which is the original revision for the PR diff.
- `head_sha`: the proposed candidate revision, including public fork heads.
- Changed-file names, rename origins, change counts, and blob identities.
- Raw diff, its SHA-256, capture time, and explicit limitations.
- Review observations for test-file changes, file removals, and test configuration.

The collector requests comparisons by full SHA, verifies aggregate file counts,
and rechecks PR metadata before accepting the bundle. It also rechecks repository
identity/visibility. A moving PR fails with `pr_changed`; it never silently mixes
revisions. GitHub may omit binary contents, so a diff alone is not a full checkout.

Current intake limits: **300 changed files per PR**, 8 MiB per JSON response,
4 MiB per diff, 15-second request timeouts, and a 90-second total deadline. The file
limit comes from GitHub's pinned comparison endpoint; it does not limit the number
of files in the whole repository. These are prototype limits, not claims of full
coverage on arbitrarily large changes.

All 13 correctness checks begin as `not_run`. After intake, test integrity becomes
`needs_review` if test/configuration paths changed, otherwise `unknown`. Filename
heuristics cannot prove assertion strength, absence of hardcoding, or correctness.
No check is assigned `pass` by intake. `confidence` stays null and
`execution_status` starts as `not_run`; queued execution updates it independently
of intake state. See [execution jobs](execution-jobs.md) for the evaluator API.

## Storage and exports

SQLite supports local development; Compose uses PostgreSQL. Alembic migrations run
at startup and can also be applied with `python -m codehound.db upgrade` after
setting the database environment. PostgreSQL migration startup uses an advisory
lock. Lists are paginated, ordered by creation time and ID, and do not load large
diff payloads. Detail and export endpoints require ownership and disable caching.

The standalone fixture and PR-comparison runners write separate execution
artifacts. They do not change a saved PR's execution status. This separation avoids
assigning real test results to unrelated sample or draft data.
