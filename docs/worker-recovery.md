# Worker crash recovery

Each database receives a stable random execution namespace in migration `0003_execution_namespace`. Job containers are labelled with that namespace, their job ID, and their claim token. Managed checkouts carry the same identity in a controller-owned marker outside both repository worktrees.

The worker checks for abandoned resources every 60 seconds. A resource is eligible only when it belongs to this database, is at least 120 seconds old, and its exact claim has no unexpired running lease. It rechecks the lease before removal. Normal completion and cancellation still clean up immediately.

This recovers resources after a process crash or forced termination. It does not treat a live but slow job as abandoned. Different database namespaces, unlabelled containers, malformed markers, recent resources, symlinked workspace entries, and unrelated files are left alone. Resources created by older workers or standalone CLI runs lack claim labels and are not automatically removed.

## Inspect or clean manually

From the repository root:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m codehound.execution.cleanup --env-file .env
```

This is a dry run. Add `--apply` to remove eligible resources. `--grace-seconds` accepts 0–86400; lowering it never overrides a live lease. The command returns JSON with eligible/removed resources and sanitized errors, and exits nonzero if container inventory or deletion failed.

Worker checkouts now live under `data/workspaces/<database-namespace>/` by default. `CODEHOUND_DATA_DIR` changes the data root; `CODEHOUND_WORKSPACE_DIR` overrides the workspace root. All workers sharing a database should use the same workspace root to recover each other's checkouts. Cleanup only sees the current host's Docker daemon and filesystem.

Run only one deployment against a database. Copying a database also copies its execution namespace, so two deployments using a copied database on the same Docker daemon would share resource ownership. This is lifecycle recovery, not a disk quota or a stronger container security boundary.
