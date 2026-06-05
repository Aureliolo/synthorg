"""Shared helpers for the SQLite conversation repositories."""

import sqlite3

import aiosqlite

from synthorg.observability import get_logger, log_exception_redacted

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    event: str,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure.

    A bare ``await db.rollback()`` in an ``except`` block can itself
    raise on the shared connection, masking the original domain error.
    This helper logs the rollback failure under its own structured
    event and swallows it so the caller can re-raise the root cause.
    ``MemoryError`` / ``RecursionError`` propagate unchanged.
    """
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            event,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


__all__ = ["_MAX_PAGE_LIMIT", "_safe_rollback"]
