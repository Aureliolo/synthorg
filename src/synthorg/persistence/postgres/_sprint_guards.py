# module-kind: code
"""Turning a psycopg failure into this layer's domain error.

Every write on the sprint repository fails the same two ways and owes the
same pair in the same order (log, raise the right one), so the sequence
lives here once rather than five times in the repository. Per call site only
the words differ.

Split from the repository for the same reason its statements are: this is
error translation, which is a different kind of thing from the CRUD it
wraps, and the SQLite sibling (``sqlite/_sprint_guards.py``) expresses the
same contract over a different driver's exception hierarchy. The one real
difference is that the pool's connection context rolls the transaction back
on the way out, so unlike SQLite there is nothing here to unwind by hand.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.sprint import PERSISTENCE_SPRINT_FAILED

logger = get_logger(__name__)


@asynccontextmanager
async def write_guard(
    *, operation: str, doing: str, sprint_id: str
) -> AsyncIterator[None]:
    """Log and re-raise a write failure as its domain error.

    Args:
        operation: The repository method, for the structured log.
        doing: The present participle naming the attempt in the message an
            operator reads (``saving``, ``transitioning``, ...).
        sprint_id: The row the write targeted.

    Yields:
        Nothing; the caller runs its statements inside the guard.

    Raises:
        ConstraintViolationError: On a constraint violation.
        QueryError: On any other driver error.
    """
    try:
        yield
    except psycopg.errors.IntegrityError as exc:
        msg = (
            f"Constraint violation {doing} sprint {sprint_id!r}: "
            f"{safe_error_description(exc)}"
        )
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            sprint_id=sprint_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise ConstraintViolationError(msg, constraint=str(exc)) from exc
    except psycopg.Error as exc:
        msg = (
            f"Failed {doing} sprint {sprint_id!r}: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            sprint_id=sprint_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


@asynccontextmanager
async def read_guard(
    *, operation: str, failure: str, **log_context: object
) -> AsyncIterator[None]:
    """Re-raise a read failure as :class:`QueryError`, logged once.

    The write sibling's second arm alone: a read has no constraint to
    violate. A ``QueryError`` raised inside (a row this layer could not
    parse) passes straight through rather than being re-wrapped into a
    vaguer one.

    Args:
        operation: The repository method, for the structured log.
        failure: The message an operator reads.
        **log_context: Extra structured fields (e.g. ``sprint_id``).

    Yields:
        Nothing; the caller runs its statements inside the guard.

    Raises:
        QueryError: On any driver error.
    """
    try:
        yield
    except QueryError:
        raise
    except psycopg.Error as exc:
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **log_context,
        )
        raise QueryError(failure) from exc


__all__ = ["read_guard", "write_guard"]
