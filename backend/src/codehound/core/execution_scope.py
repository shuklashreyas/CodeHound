"""Controller-owned resource identity, propagated through an execution's async tasks."""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class ExecutionScope:
    namespace: str
    job_id: str
    claim_token: str

    def __post_init__(self):
        for value in (self.namespace, self.job_id, self.claim_token):
            if str(UUID(value)) != value:
                raise ValueError("Execution resource identities must be canonical UUIDs.")

    def labels(self):
        return {
            "codehound.namespace": self.namespace,
            "codehound.job": self.job_id,
            "codehound.claim": self.claim_token,
        }


current_scope: ContextVar[ExecutionScope | None] = ContextVar(
    "codehound_execution_scope", default=None
)


@contextmanager
def execution_scope(scope):
    token = current_scope.set(scope)
    try:
        yield
    finally:
        current_scope.reset(token)


def workspace_root():
    project = Path(__file__).resolve().parents[4]
    if not (project / "backend" / "pyproject.toml").is_file():
        project = Path.cwd()
    data = Path(os.getenv("CODEHOUND_DATA_DIR", str(project / "data")))
    return (
        Path(os.getenv("CODEHOUND_WORKSPACE_DIR", str(data / "workspaces"))).expanduser().resolve()
    )
