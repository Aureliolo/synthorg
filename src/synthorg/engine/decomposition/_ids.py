# module-kind: code
"""Subtask-id -> child-task-UUID parsing, shared across the decomposition layer.

A leaf module so both the decomposition service (which mints child tasks) and
the plan projection (which rebuilds them from a durable plan at dispatch time)
canonicalise ids identically, without either pulling the heavy service module.
"""

from uuid import UUID

from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import DECOMPOSITION_FAILED

logger = get_logger(__name__)


def subtask_uuid(subtask_id: str) -> UUID:
    """Parse a subtask id into the canonical UUID used for the child task.

    Decomposition strategies remap throwaway labels to canonical UUID strings
    before a plan is persisted, so the id always parses here; a non-canonical
    id would yield two textual ids for one subtask and break string-based
    correlation, so it is rejected loudly.

    Args:
        subtask_id: The plan subtask id to convert.

    Returns:
        The id as a ``UUID``.

    Raises:
        DecompositionError: When ``subtask_id`` is not a canonical UUID string.
    """
    try:
        parsed = UUID(subtask_id)
    except ValueError as exc:
        msg = (
            f"Subtask id {subtask_id!r} is not a valid UUID string; "
            "decomposition strategies must supply UUID-string subtask ids"
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            reason="subtask_id_not_uuid",
            error_type=DecompositionError.__name__,
            error=safe_error_description(exc),
        )
        raise DecompositionError(msg) from exc
    canonical = str(parsed)
    if subtask_id != canonical:
        msg = (
            f"Subtask id {subtask_id!r} is not in canonical UUID form; "
            f"use {canonical!r}"
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            reason="subtask_id_not_canonical",
            error_type=DecompositionError.__name__,
        )
        raise DecompositionError(msg)
    return parsed
