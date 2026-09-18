# Architecture and implementation boundaries

CodeHound asks whether an automated verifier can detect incorrect AI-generated
patches that still pass visible tests.

## Planned verification flow

Repository + base commit + issue + candidate patch
→ isolated original and patched workspaces
→ visible tests, hidden tests, static checks, and integrity checks
→ saved execution evidence
→ per-dimension judgments and a reviewable report.

The original revision provides a baseline so pre-existing failures are not
attributed to the candidate patch. Missing checks must be reported as unknown.
Numerical confidence will require calibration against independently labeled data.

## Boundaries

- `api`: request validation, evaluation endpoints, and report retrieval.
- `core`: shared configuration and domain types.
- `repositories`: revision checkout, diffs, and patch application.
- `evaluation`: execution orchestration and evidence aggregation.
- PostgreSQL: planned persistence for tasks, runs, judgments, and artifact metadata.
- `data/`: ignored local execution artifacts.
- Frontend: issue, patch, evidence, and final verification views.

API liveness and an interactive frontend with sample reports and session-only
submission drafts are implemented. GitHub OAuth connects the frontend to public repository and open-PR metadata.
Repository checkout and the evaluation runner are not implemented.
The database is provisioned locally but has no schema or application integration yet.

## Execution isolation

The API container is infrastructure, not a sandbox for candidate code. A future
runner must use disposable, restricted containers with time and resource limits,
controlled network access, no host secrets, and no host Docker socket exposed to
candidate code. Hidden tests and trusted result collection must be isolated from
candidate-controlled test configuration. Do not execute candidate patches in the
API process or directly on the host.

## Research evaluation

Compare visible-tests-only, LLM review, static rules plus tests, and CodeHound.
Keep verifier-accessible hidden tests separate from held-out benchmark checks.
Split related tasks and patches together to reduce leakage. Measure detection of
invalid patches among visible-test-passing submissions alongside false positives
on valid patches. Include human-reviewed labels and reproducible evidence.

## Next milestones

1. Define task, patch, run, evidence, and judgment schemas with database migrations.
2. Implement repository checkout and patch application in disposable workspaces.
3. Add isolated execution with baseline comparisons and saved stdout/stderr.
4. Add hidden-test execution and test-integrity checks.
5. Build report views and a small reproducible benchmark before learned models.

## GitHub authentication boundary

The API owns the OAuth client secret, PKCE verifier, access token, and expiring
session data. The browser receives only an opaque HttpOnly cookie and public
profile/repository metadata. OAuth flows are bound to a random browser cookie and
consumed once; sign-out requires a same-origin custom-header request. Authenticated
API responses are marked no-store. Public-only filtering also applies to PR access.
The process-local session store is suitable only for this single-worker development
scaffold; deployment requires a shared expiring store and HTTPS.
