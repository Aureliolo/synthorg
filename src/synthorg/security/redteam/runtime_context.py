"""Trusted per-evaluation runtime context for the red-team gate.

The gate binds the deliverable's ``(execution_id, task_id)`` to a
contextvar just before invoking the red-team agent and clears it
after. The :func:`submit_red_team_report` tool reads that context and
rejects payloads whose ``execution_id`` / ``task_id`` do not match.

Without this defense an agent-side prompt injection could coerce the
agent into filing a report under a different execution -- the
agent-supplied identifiers reach the tool through tool-call arguments
that the agent fully controls. Pinning the truth at the gate-set
contextvar moves the trust boundary inside the host process.
"""

from collections.abc import (
    Iterator,
)
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class RedTeamRuntimeContext(BaseModel):
    """Trusted IDs the gate seeds before the agent runs."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr


_RED_TEAM_RUNTIME_CONTEXT: Final[ContextVar[RedTeamRuntimeContext | None]] = ContextVar(
    "synthorg.security.redteam.runtime_context", default=None
)


def get_red_team_runtime_context() -> RedTeamRuntimeContext | None:
    """Return the trusted context for the current task, or ``None`` outside it."""
    return _RED_TEAM_RUNTIME_CONTEXT.get()


@contextmanager
def red_team_runtime_context(ctx: RedTeamRuntimeContext) -> Iterator[None]:
    """Bind ``ctx`` for the duration of the with-block.

    Contextvars propagate to child tasks spawned during the block, so
    every code path the agent runner traverses (engine, tool dispatch)
    observes the trusted IDs without explicit plumbing.
    """
    token = _RED_TEAM_RUNTIME_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _RED_TEAM_RUNTIME_CONTEXT.reset(token)
