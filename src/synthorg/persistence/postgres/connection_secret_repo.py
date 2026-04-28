"""Postgres-backed encrypted-secret blob repository.

Persists raw encrypted bytes in the ``connection_secrets`` table
(``encrypted_value BYTEA``) for the ``EncryptedPostgresSecretBackend``.
This layer NEVER encrypts or decrypts; callers pass already-encrypted
bytes in and receive the same bytes back unchanged.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONNECTION_SECRET_DELETE_FAILED,
    PERSISTENCE_CONNECTION_SECRET_RETRIEVE_FAILED,
    PERSISTENCE_CONNECTION_SECRET_STORE_FAILED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr


logger = get_logger(__name__)


class PostgresConnectionSecretRepository:
    """Postgres implementation of :class:`ConnectionSecretRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Bind to the shared *pool*."""
        self._pool = pool

    async def store(
        self,
        secret_id: NotBlankStr,
        encrypted_value: bytes,
        key_version: int,
    ) -> None:
        """Persist an encrypted secret blob (upsert by ``secret_id``)."""
        now = datetime.now(UTC)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO connection_secrets (
                        secret_id, encrypted_value, key_version,
                        created_at, rotated_at
                    ) VALUES (%s, %s, %s, %s, NULL)
                    ON CONFLICT (secret_id) DO UPDATE SET
                        encrypted_value = EXCLUDED.encrypted_value,
                        key_version = EXCLUDED.key_version,
                        rotated_at = EXCLUDED.created_at
                    """,
                    (str(secret_id), encrypted_value, int(key_version), now),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to store connection secret {secret_id!r}"
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT encrypted_value FROM connection_secrets "
                    "WHERE secret_id = %s",
                    (str(secret_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM connection_secrets WHERE secret_id = %s",
                    (str(secret_id),),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete connection secret {secret_id!r}"
            logger.warning(
                PERSISTENCE_CONNECTION_SECRET_DELETE_FAILED,
                secret_id=str(secret_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
