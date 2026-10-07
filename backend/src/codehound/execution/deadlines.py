"""Task-local work budget, leaving the worker hard deadline for resource cleanup."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from codehound.execution.provenance import bind_source

_SOURCE_BINDING = bind_source(__file__)

SOFT_TIMEOUT_SECONDS = 560
HARD_TIMEOUT_SECONDS = 600
SUITE_CLEANUP_SECONDS = 20


@dataclass
class ExecutionBudget:
    seconds: float
    deadline: float
    incomplete_stages: list[str] = field(default_factory=list)

    def remaining(self, *, reserve=0):
        return max(0, self.deadline - asyncio.get_running_loop().time() - reserve)

    def incomplete(self, stage):
        if stage not in self.incomplete_stages:
            self.incomplete_stages.append(stage)

    def to_dict(self):
        return {
            "status": "exhausted" if self.incomplete_stages else "completed",
            "work_timeout_seconds": self.seconds,
            "suite_cleanup_reserve_seconds": SUITE_CLEANUP_SECONDS,
            "incomplete_stages": list(self.incomplete_stages),
        }


_BUDGET = ContextVar("codehound_execution_budget", default=None)


def current_budget():
    return _BUDGET.get()


@contextmanager
def execution_budget(seconds=SOFT_TIMEOUT_SECONDS):
    if seconds <= 0:
        raise ValueError("Execution budget must be positive.")
    budget = ExecutionBudget(seconds, asyncio.get_running_loop().time() + seconds)
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)


async def optional_stage(stage, operation, fallback):
    """Only convert our own deadline expiration; cancellation and internal errors propagate."""
    budget = current_budget()
    if budget is None:
        return await operation()
    if not budget.remaining():
        budget.incomplete(stage)
        return fallback()
    timer = asyncio.timeout_at(budget.deadline)
    try:
        async with timer:
            return await operation()
    except TimeoutError:
        if not timer.expired():
            raise
        budget.incomplete(stage)
        return fallback()
