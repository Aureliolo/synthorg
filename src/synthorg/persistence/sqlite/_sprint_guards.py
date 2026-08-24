# module-kind: code
"""Turning a SQLite driver failure into this layer's domain error.

Every write on the sprint repository fails the same two ways and owes the
same three things in the same order (roll back, log, raise the right one),
so the sequence lives here once rather than five times in the repository.
Per call site only the words differ.

Split from the repository for the same reason its statements are: this is
error translation, which is a different kind of thing from the CRUD it
wraps, and the Postgres sibling (``postgres/_sprint_guards.py``) expresses
the same contract over a different driver's exception hierarchy.
"""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence.sprint import PERSISTENCE_SPRINT_FAILED

logger = get_logger(__name__)


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            PERSISTENCE_SPRINT_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


@asynccontextmanager
async def write_guard(
    db: aiosqlite.Connection,
    *,
    operation: str,
    doing: str,
    sprint_id: str,
) -> AsyncIterator[None]:
    """Roll back, log, and re-raise a write failure as its domain error.

    Args:
        db: The connection whose transaction is rolled back on failure.
        operation: The repository method, for the structured log.
        doing: The present participle naming the attempt in the message
            an operator reads (``saving``, ``transitioning``, ...).
        sprint_id: The row the write targeted.

    Anything else raised inside unwinds the write too, and propagates
    unchanged. A statement's own RETURNING row can be one the domain model
    refuses, and that is not a database failure to be dressed as one; but
    it does mean the write must not stand, because a committed row nothing
    can parse is unreadable for good, to every later reader including the
    recovery sweep.

    Yields:
        Nothing; the caller runs its statements inside the guard.

    Raises:
        ConstraintViolationError: On a constraint violation.
        QueryError: On any other driver error.
    """
    try:
        yield
    except sqlite3.IntegrityError as exc:
        await _safe_rollback(db, operation=operation, sprint_id=sprint_id)
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
    except (sqlite3.Error, aiosqlite.Error) as exc:
        await _safe_rollback(db, operation=operation, sprint_id=sprint_id)
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
    except Exception:
        # Not a driver failure, so it is not re-dressed as one and not
        # logged here (whoever raised it owns saying why). The rollback is
        # the point: SQLite's transaction is open on the shared
        # connection, so without it the write stands and the next writer
        # inherits it.
        await _safe_rollback(db, operation=operation, sprint_id=sprint_id)
        raise


@asynccontextmanager
async def read_guard(
    *, operation: str, failure: str, **log_context: object
) -> AsyncIterator[None]:
    """Re-raise a read failure as :class:`QueryError`, logged once.

    The write sibling's second arm without the rollback: a read opened no
    transaction, so there is nothing to unwind. A ``QueryError`` raised
    inside (a row this layer could not parse) passes straight through
    rather than being re-wrapped into a vaguer one.

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
    except (sqlite3.Error, aiosqlite.Error) as exc:
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **log_context,
        )
        raise QueryError(failure) from exc


__all__ = ["read_guard", "write_guard"]
