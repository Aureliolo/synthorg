"""SQLite ciphertext row store for the encrypted secret backend.

Stores Fernet ciphertext blobs in the ``connection_secrets`` table via
``aiosqlite``. ``upsert`` is idempotent through ``INSERT OR REPLACE``.
This is the SQLite arm of the
:class:`~synthorg.persistence.secret_backends.row_store.SecretRowStore`
seam composed by
:class:`~synthorg.persistence.secret_backends.encrypted.EncryptedSecretBackend`.
"""

from typing import override

import aiosqlite

from synthorg.core.critical_errors import reraise_critical
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


class SqliteSecretRowStore(SecretRowStore):
    """Persist encrypted secret rows in a SQLite database.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @property
    @override
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return "encrypted_sqlite"

    @override
    async def upsert(self, secret_id: NotBlankStr, ciphertext: bytes) -> None:
        """Insert or replace the ciphertext row for *secret_id*.

        Idempotent via ``INSERT OR REPLACE``: an existing row with the
        same ``secret_id`` has its ciphertext overwritten.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO connection_secrets "
                    "(secret_id, encrypted_value, key_version, "
                    "created_at, rotated_at) "
                    "VALUES (?, ?, 1, datetime('now'), NULL)",
                    (secret_id, ciphertext),
                )
                await db.commit()
        except Exception as exc:
            reraise_critical(exc)
            # ``warning`` + scrubbed description: driver exceptions may
            # embed connection URIs or raw Fernet ciphertext bytes.
            # Traceback attachment via ``logger.exception`` would also
            # serialize the request-level ciphertext we just wrote.
            # Scrubbed type+message is enough for triage; ``raise ...
            # from exc`` preserves the chain for callers.
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
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT encrypted_value FROM connection_secrets "
                    "WHERE secret_id = ?",
                    (secret_id,),
                )
                row = await cursor.fetchone()
        except Exception as exc:
            reraise_critical(exc)
            # ``warning`` + scrubbed description: same rationale as
            # ``upsert`` above. A DB driver failure may embed the row's
            # encrypted ciphertext in the exception; ``logger.exception``
            # would serialize it via traceback.
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
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM connection_secrets WHERE secret_id = ?",
                    (secret_id,),
                )
                await db.commit()
                deleted = cursor.rowcount > 0
        except Exception as exc:
            reraise_critical(exc)
            # Driver exceptions may embed connection URIs with
            # credentials; scrub + drop traceback.  Use the
            # ``SECRET_DELETE_FAILED`` event (not ``STORAGE_FAILED``)
            # so delete failures stay distinguishable from writes.
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
        """No-op for SQLite (connections are per-call)."""
