"""SQLite-backed encrypted-secret blob repository.

Persists raw encrypted byte payloads in the ``connection_secrets``
table for the ``EncryptedSqliteSecretBackend``.  This layer NEVER
encrypts or decrypts -- callers pass already-encrypted bytes in and
receive the same bytes back unchanged.
"""

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONNECTION_SECRET_DELETE_FAILED,
    PERSISTENCE_CONNECTION_SECRET_RETRIEVE_FAILED,
    PERSISTENCE_CONNECTION_SECRET_STORE_FAILED,
)
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence.errors import QueryError

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class SQLiteConnectionSecretRepository:
    """SQLite implementation of :class:`ConnectionSecretRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        """Bind to *db* and serialize writes via *write_lock*."""
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def store(
        self,
        secret_id: NotBlankStr,
        encrypted_value: bytes,
        key_version: int,
    ) -> None:
        """Persist an encrypted secret blob (upsert by ``secret_id``).

        The repository stores the bytes verbatim; encryption happens
        at the secret-backend layer above.
        """
        now_iso = format_iso_utc(datetime.now(UTC))
        async with self._write_lock:
            try:
                await self._db.execute(
                    """
                    INSERT INTO connection_secrets (
                        secret_id, encrypted_value, key_version,
                        created_at, rotated_at
                    ) VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(secret_id) DO UPDATE SET
                        encrypted_value = excluded.encrypted_value,
                        key_version = excluded.key_version,
                        rotated_at = excluded.created_at
                    """,
                    (str(secret_id), encrypted_value, int(key_version), now_iso),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to store connection secret {secret_id!r}"
                # Avoid embedding secret_id details further; treat as PII-adjacent.
                logger.warning(
                    PERSISTENCE_CONNECTION_SECRET_STORE_FAILED,
                    secret_id=str(secret_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        """Return the raw encrypted bytes, or ``None`` if not stored."""
        try:
            async with self._db.execute(
                "SELECT encrypted_value FROM connection_secrets WHERE secret_id = ?",
                (str(secret_id),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to retrieve connection secret {secret_id!r}"
            logger.warning(
                PERSISTENCE_CONNECTION_SECRET_RETRIEVE_FAILED,
                secret_id=str(secret_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return bytes(row[0])

    async def delete(self, secret_id: NotBlankStr) -> bool:
        """Delete an encrypted secret; return ``True`` if a row was removed."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM connection_secrets WHERE secret_id = ?",
                    (str(secret_id),),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete connection secret {secret_id!r}"
                logger.warning(
                    PERSISTENCE_CONNECTION_SECRET_DELETE_FAILED,
                    secret_id=str(secret_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
