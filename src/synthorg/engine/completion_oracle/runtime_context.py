# module-kind: code
"""Trusted per-evaluation runtime context for the peer-review gate.

The gate binds the deliverable's ``(execution_id, task_id)`` to a
contextvar just before invoking the reviewer agent and clears it after.
The :func:`submit_completion_oracle_verdict` tool reads that context and
rejects payloads whose ``execution_id`` / ``task_id`` do not match.

Without this defence an agent-side prompt injection could coerce the
reviewer into filing a verdict under a different execution: the
agent-supplied identifiers reach the tool through tool-call arguments the
agent fully controls. Pinning the truth at the gate-set contextvar moves
the trust boundary inside the host process.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class CompletionOracleRuntimeContext(BaseModel):
    """Trusted IDs the gate seeds before the reviewer agent runs.

    The reviewer and executor identities are seeded here, not taken from
    the tool's arguments, so an agent-side prompt injection cannot spoof
    who reviewed or who is being reviewed: the submit tool stamps the
    report from these trusted values.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    reviewer_agent_id: NotBlankStr
    executor_agent_id: NotBlankStr


_RUNTIME_CONTEXT: Final[ContextVar[CompletionOracleRuntimeContext | None]] = ContextVar(
    "synthorg.engine.completion_oracle.runtime_context", default=None
)


def get_completion_oracle_runtime_context() -> CompletionOracleRuntimeContext | None:
    """Return the trusted context for the current task, or ``None`` outside it."""
    return _RUNTIME_CONTEXT.get()


@contextmanager
def completion_oracle_runtime_context(
    ctx: CompletionOracleRuntimeContext,
) -> Iterator[None]:
    """Bind ``ctx`` for the duration of the with-block.

    Contextvars propagate to child tasks spawned during the block, so every
    code path the reviewer runner traverses (engine, tool dispatch) observes
    the trusted IDs without explicit plumbing.
    """
    token = _RUNTIME_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _RUNTIME_CONTEXT.reset(token)
