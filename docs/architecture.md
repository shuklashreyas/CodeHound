# Architecture and current boundaries

CodeHound asks whether independent verification can detect incorrect patches that
pass visible tests. Generation and verification must remain separate.

## Implemented flow

```text
GitHub OAuth → public repository / PR selection
                        ↓
              account-owned saved draft
                        ↓ explicit intake API call
          immutable PR evidence + review observations

captured PR → durable execution job → worker → pinned Git workspaces
           → baseline/candidate Docker tests → stored comparison evidence
trusted fixture code + independent tests → restricted Docker runner → JSON evidence
```

Drafts, intake, queued execution, cancellation, and results are persisted. The
execution API accepts only a matching operator-owned profile ID. A separate worker
claims jobs with expiring leases and keeps assertions outside candidate containers.
The frontend connects this flow and keeps its illustrative sample separate from real execution evidence. None of these components implements a
calibrated ML judge, general static analyzer, or semantic requirement verifier yet.
Changed Python test structure is inspected in an isolated parser; see
[test-integrity.md](test-integrity.md).

## Components

- `api`: authentication, owner-scoped verification endpoints, readiness.
- `db`: SQLAlchemy storage, migrations, short transactions, intake/execution leases.
- `repositories`: strict URL validation, bounded GitHub requests, snapshot capture,
  and temporary Git workspaces pinned to exact commits.
- `evaluation`: submission/report contracts and explicit unrun check states.
- `execution`: independent evaluator, restricted Docker runner, worker, comparison CLI.
- `data/`: ignored local SQLite database and local evidence files.

SQLite keeps development usable without Docker. Compose uses PostgreSQL. Execution
images are built from trusted definitions, resolved to immutable local IDs, and
never selected or built from untrusted HTTP input.

## Isolation

Git fetches public commit SHAs without inheriting credentials, Git configuration,
SSH agents, hooks, or submodule recursion. Repository code is not executed during
checkout. Checkouts have a total deadline, sampled size/file limits, and cleanup
on normal completion or errors. Sampled size checks are not disk quotas;
production workers need dedicated storage quotas and further network restrictions.

The Docker runner uses no network, a read-only root, dropped capabilities, a
non-root UID, bounded CPU/memory/processes, and a small temporary filesystem.
Workspaces, independent tests, and the trusted harness are read-only mounts. It does
not mount the host Docker socket or pass host credentials into the container.
Candidate pytest config and conftest files do not control test discovery. Both an
in-container deadline and an outer watchdog bound runtime; output is capped and
container removal is attempted in a final cleanup block. Host or daemon failure
can interrupt cleanup; production workers also need orphan-container reconciliation.

Python under test still shares a process with pytest. It can attempt to manipulate
the test framework or terminate the process. Exit codes and logs are evidence, not
proof against adversarial code. The JSON function evaluator keeps assertions and
expected answers outside the candidate container. Its narrower contract and remaining limits are
documented in `independent-evaluator.md`; arbitrary pytest does not gain that boundary.

## Authentication

OAuth state and PKCE bind sign-in to a browser. Access tokens are stored only in
process-local server sessions; the browser gets an opaque HttpOnly cookie. HTTPS
origins use Secure cookies. Requests that mutate records require a custom header
and the configured origin. Public-only constraints apply to metadata and PR intake.
Saved ownership uses immutable GitHub IDs. Restarting the API signs users out but
does not delete saved drafts. Multi-worker deployments need a shared session store.

## Research evaluation

Keep verifier-accessible tests separate from held-out benchmark checks. Split
related tasks and patches together to avoid leakage. Measure false positives as
well as detection among visible-test-passing patches. Retain human-reviewed labels,
baseline failures, environment identities, and reproducible artifacts.

The pagination fixture is public and synthetic. It demonstrates a correct fix
versus an overfit one, not a benchmark result. Numerical confidence requires
calibration against independently labeled data.

## Next milestones

1. Build a reproducible labeled benchmark and compare visible-only detection.
2. Add operator-owned profiles for more repository contracts.
3. Broaden structural integrity coverage and add general static-analysis results.
4. Harden worker storage quotas, account quotas, and orphan cleanup.
5. Build a labeled benchmark before training learned evaluators.
