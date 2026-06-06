# module-kind: code
"""Shared base for the SQLite decision-repository aspect mixins."""

import sqlite3

import aiosqlite

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.decision_record import (
    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


class _DecisionRepoBase:
    """Connection + write-serialization seam shared by the aspect mixins.

    Holds the open ``aiosqlite.Connection`` and the backend
    ``write_context`` that serializes the multi-statement
    ``INSERT -> SELECT -> commit/rollback`` sequences so concurrent
    coroutines cannot interleave their statements or have one
    coroutine's rollback wipe another's in-flight INSERT.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes
            multi-statement transactions on ``db``.
    """

    _db: aiosqlite.Connection
    _write_context: WriteContext

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _rollback_quietly(self) -> None:
        """Roll back the current transaction, swallowing rollback errors.

        If the rollback itself fails (e.g. connection dropped), we log
        the secondary failure but do not shadow the caller's original
        exception -- that's the one the caller needs to see.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                stage="rollback",
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
            )
