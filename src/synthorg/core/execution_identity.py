"""Execution-identity context seam.

A :mod:`contextvars`-backed seam carrying the *run* an agent is
currently executing: its ``execution_id`` (the same value stamped on
:class:`~synthorg.persistence.flight_recorder_protocol.FlightRecorderFrame`),
``task_id``, and ``project_id``. Bound once by the engine around an agent
run and read at capture leaves (knowledge retrieval, code execution) so
provenance records key on the same ``execution_id`` the deliverable
receipt later joins on.

The contextvar holds ``None`` outside any bound scope; capture leaves
treat a ``None`` binding as "not inside a tracked run" and skip recording
rather than inventing identifiers. Natively async-aware, so child tasks
spawned inside the scope inherit the binding.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr

__all__ = [
    "ExecutionIdentity",
    "current_execution_identity",
    "execution_identity_scope",
    "run_identity_scope",
]


class ExecutionIdentity(BaseModel):
    """The run a capture leaf should attribute a provenance record to.

    Attributes:
        execution_id: Run identifier, identical to the value the engine
            stamps on flight-recorder frames for the same run.
        task_id: Task the run is working on.
        project_id: Owning project, when the task is project-scoped.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    project_id: NotBlankStr | None = None


_execution_var: ContextVar[ExecutionIdentity | None] = ContextVar(
    "synthorg_execution_identity",
    default=None,
)


def current_execution_identity() -> ExecutionIdentity | None:
    """Return the bound execution identity, or ``None`` outside a run.

    Returns:
        The bound :class:`ExecutionIdentity`, or ``None`` when no run
        scope is active (capture leaves skip recording in that case).
    """
    return _execution_var.get()


@contextmanager
def execution_identity_scope(
    identity: ExecutionIdentity,
) -> Iterator[None]:
    """Bind *identity* for the block, restoring the prior value on exit.

    Args:
        identity: The run identity to bind for the duration of the
            block.

    Yields:
        ``None``; the binding is active for the body.
    """
    token = _execution_var.set(identity)
    try:
        yield
    finally:
        _execution_var.reset(token)


@contextmanager
def run_identity_scope(
    *,
    execution_id: NotBlankStr,
    task_id: str,
    project_id: str | None,
) -> Iterator[None]:
    """Bind a run identity built from raw run fields for the block.

    Convenience over :func:`execution_identity_scope` for the engine's
    run boundary: constructs the :class:`ExecutionIdentity` (validating
    the identifiers) from the same ``execution_id`` the flight recorder
    stamps, so capture leaves and the deliverable receipt share one join
    key. Restores the prior binding on exit.

    Args:
        execution_id: The run's execution identifier.
        task_id: Task the run is working on.
        project_id: Owning project, when the task is project-scoped.

    Yields:
        ``None``; the binding is active for the body.
    """
    identity = ExecutionIdentity(
        execution_id=execution_id,
        task_id=NotBlankStr(task_id),
        project_id=NotBlankStr(project_id) if project_id else None,
    )
    with execution_identity_scope(identity):
        yield
