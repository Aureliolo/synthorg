# module-kind: code
"""The live-run half of "is this agent working", read once for every surface.

:func:`~synthorg.core.task_activity.busy_agent_ids` is the resolver, and it
takes two inputs: the task board, and the agents holding a live agent-state
row. The board is in hand wherever the question is asked; the live rows need
persistence, so they are read here rather than re-derived per controller.

One module because the answer has to be the same one everywhere. The org
overview and a department's health card both report who is working, and a
planning session is invisible to the board for its whole life, so a surface
reading the board alone reports the same agent as idle while its sibling
reports it busy. That divergence is not a display difference: the department
card also derives utilisation from the count, and asserts it is not degraded.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.pagination import collect_all
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


async def running_agent_ids(app_state: AppState) -> frozenset[str] | None:
    """Return the agents holding a live run, or ``None`` when unreadable.

    The one query that knows an agent is working right now, and until this
    read it had no caller anywhere: every surface answering "is the org
    working" derived it from task status instead, which cannot see a run whose
    task is not ``IN_PROGRESS``.

    ``None`` rather than an empty set on a failure, because the two are
    different claims: an empty set says nobody is running, and ``None`` says
    nobody could be asked. Callers that report a count to an operator carry
    that distinction into what they report, rather than presenting a board-only
    count as the whole answer.

    Args:
        app_state: The application state to resolve persistence from.

    Returns:
        The busy agent ids, or ``None`` when persistence cannot answer.
    """
    try:
        backend = persistence_of(app_state)
    except ServiceUnavailableError:
        logger.debug(
            API_REQUEST_ERROR,
            endpoint="running_agent_ids",
            reason="persistence_unconnected",
        )
        return None
    try:
        # Drained rather than paged: the caller is counting every agent that is
        # working, and a page would silently stop counting at its limit and
        # report the truncation as an answer.
        states = await collect_all(
            lambda limit, offset: backend.agent_states.get_active(
                limit=limit, offset=offset
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="running_agent_ids",
            reason="agent_state_query_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return frozenset(str(state.agent_id) for state in states)


__all__ = ["running_agent_ids"]
