"""Postgres ciphertext row store for the encrypted secret backend.

Stores Fernet ciphertext blobs in the ``connection_secrets`` table
(``encrypted_value BYTEA``) via ``psycopg`` / ``psycopg_pool``.
``upsert`` is idempotent through ``ON CONFLICT ... DO UPDATE``. This is
the Postgres arm of the
:class:`~synthorg.persistence.secret_backends.row_store.SecretRowStore`
seam composed by
:class:`~synthorg.persistence.secret_backends.encrypted.EncryptedSecretBackend`.

The ``pool`` argument accepts either a concrete ``AsyncConnectionPool``
(convenient for tests that already own a connected pool) or a zero-arg
callable that returns one (used by ``create_app`` so the pool is acquired
lazily on the first operation, after ``persistence.connect()`` has
succeeded in the startup lifecycle).
"""

from collections.abc import Callable
from typing import override

import psycopg
from psycopg_pool import AsyncConnectionPool

from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import (
    SecretRetrievalError,
    SecretStorageError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    SECRET_DELETE_FAILED,
    SECRET_RETRIEVAL_FAILED,
    SECRET_STORAGE_FAILED,
)
from synthorg.persistence.secret_backends.row_store import SecretRowStore

logger = get_logger(__name__)


class PostgresSecretRowStore(SecretRowStore):
    """Persist encrypted secret rows in a shared Postgres database.

    Args:
        pool: Async Postgres connection pool, or a callable that
            returns one on demand.
    """

    def __init__(
        self,
        pool: "AsyncConnectionPool | Callable[[], AsyncConnectionPool]",  # noqa: UP037
    ) -> None:
        self._pool_or_getter = pool

    def _get_pool(self) -> "AsyncConnectionPool":  # noqa: UP037
        """Resolve the pool, whether passed concretely or via callable.

        Returns:
            Result of type ``'AsyncConnectionPool'``.
        """
        target = self._pool_or_getter
        if callable(target):
            return target()
        return target

    @property
    @override
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return "encrypted_postgres"

    @override
    async def upsert(self, secret_id: NotBlankStr, ciphertext: bytes) -> None:
        """Insert or replace the ciphertext row for *secret_id*.

        Idempotent via UPSERT: an existing row with the same
        ``secret_id`` has its ciphertext and ``rotated_at`` overwritten.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        try:
            pool = self._get_pool()
            async with pool.connection() as conn, conn.cursor() as cur:
                # Fernet uses a fresh IV on every call, so the raw
                # ciphertext differs between back-to-back ``upsert``
                # calls with the same plaintext. Detecting a "true"
                # rotation (plaintext changed) at the SQL layer would
                # require a read-decrypt-compare cycle, which doesn't
                # fit a single UPSERT. We therefore bump ``rotated_at``
                # on every write and treat it as a last-write timestamp
                # -- see the per-secret rotate() path for real rotation
                # semantics that produce a new ``secret_id``.
                await cur.execute(
                    "INSERT INTO connection_secrets "
                    "(secret_id, encrypted_value, key_version, "
                    "created_at, rotated_at) "
                    "VALUES (%s, %s, 1, NOW(), NULL) "
                    "ON CONFLICT (secret_id) DO UPDATE SET "
                    "encrypted_value = EXCLUDED.encrypted_value, "
                    "key_version = EXCLUDED.key_version, "
                    "rotated_at = NOW()",
                    (secret_id, ciphertext),
                )
                await conn.commit()
        except psycopg.Error as exc:
            # Demote to ``warning`` + scrubbed description so the
            # driver's ciphertext-bearing locals do not end up in
            # the traceback frame.
            logger.warning(
                SECRET_STORAGE_FAILED,
                secret_id=secret_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to store secret {secret_id}"
            raise SecretStorageError(msg) from exc

    @override
    async def fetch(self, secret_id: NotBlankStr) -> bytes | None:
        """Read the ciphertext row for *secret_id*.

        Returns:
            The stored ciphertext bytes, or ``None`` when absent.

        Raises:
            SecretRetrievalError: If the row is unreadable.
        """
        pool = self._get_pool()
        try:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT encrypted_value FROM connection_secrets "
                    "WHERE secret_id = %s",
                    (secret_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            logger.warning(
                SECRET_RETRIEVAL_FAILED,
                secret_id=secret_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to retrieve secret {secret_id}"
            raise SecretRetrievalError(msg) from exc

        if row is None:
            return None
        return bytes(row[0])

    @override
    async def delete(self, secret_id: NotBlankStr) -> bool:
        """Delete the ciphertext row for *secret_id*.

        Returns:
            ``True`` when a row was deleted, ``False`` if none existed.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        pool = self._get_pool()
        try:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM connection_secrets WHERE secret_id = %s",
                    (secret_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            logger.warning(
                SECRET_DELETE_FAILED,
                secret_id=secret_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to delete secret {secret_id}"
            raise SecretStorageError(msg) from exc
        else:
            return deleted

    @override
    async def close(self) -> None:
        """No-op: the pool is owned by the main persistence backend."""
