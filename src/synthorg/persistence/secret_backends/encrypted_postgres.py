"""Fernet-encrypted Postgres secret backend.

Secrets are encrypted with a Fernet key derived from the
``SYNTHORG_MASTER_KEY`` environment variable (configurable via
``EncryptedPostgresConfig.master_key_env``) and stored in the
``connection_secrets`` table of the shared Postgres database.

This is the Postgres-mode peer of
:class:`synthorg.integrations.connections.secret_backends.encrypted_sqlite.EncryptedSqliteSecretBackend`:
same on-the-wire secret format, same key rotation semantics, same
public ``SecretBackend`` protocol. The only difference is the
underlying driver -- ``psycopg``/``psycopg_pool`` instead of
``aiosqlite``.
"""

import os
from typing import TYPE_CHECKING
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet, InvalidToken

from synthorg.core.critical_errors import _reraise_critical
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.config import EncryptedPostgresConfig
from synthorg.integrations.errors import (
    MasterKeyError,
    SecretRetrievalError,
    SecretRotationError,
    SecretStorageError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    SECRET_BACKEND_UNAVAILABLE,
    SECRET_DELETE_FAILED,
    SECRET_RETRIEVAL_FAILED,
    SECRET_ROTATED,
    SECRET_STORAGE_FAILED,
    SECRET_STORED,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class EncryptedPostgresSecretBackend:
    """Fernet-encrypted Postgres secret backend.

    Secrets are stored as Fernet ciphertext blobs in the
    ``connection_secrets`` table. The master key is read from the
    environment variable specified in ``config.master_key_env``
    (default ``SYNTHORG_MASTER_KEY``).

    The ``pool`` argument accepts either a concrete
    ``AsyncConnectionPool`` (convenient for tests that already own a
    connected pool) or a zero-arg callable that returns one (used by
    ``create_app`` so the pool is acquired lazily on the first
    operation, after ``persistence.connect()`` has succeeded in the
    startup lifecycle).

    Args:
        pool: Async Postgres connection pool, or a callable that
            returns one on demand.
        config: Encrypted Postgres backend configuration.
    """

    def __init__(
        self,
        pool: "AsyncConnectionPool | Callable[[], AsyncConnectionPool]",  # noqa: UP037
        config: EncryptedPostgresConfig | None = None,
    ) -> None:
        cfg = config or EncryptedPostgresConfig()
        self._pool_or_getter = pool
        self._fernet = self._init_fernet(cfg.master_key_env)

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
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return "encrypted_postgres"

    @staticmethod
    def _init_fernet(env_var: str) -> Fernet:
        """Init fernet.

        Returns:
            Result of type ``Fernet``.

        Raises:
            MasterKeyError: If the configured master key is missing or invalid.
        """
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            msg = (
                f"{env_var} is not set. Set it to a valid Fernet key "
                f"(URL-safe base64 of 32 bytes). Generate one with: "
                f'python -c "from cryptography.fernet import Fernet; '
                f'print(Fernet.generate_key().decode())"'
            )
            raise MasterKeyError(msg)
        try:
            return Fernet(raw.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            # UnicodeEncodeError defends against accidentally pasting
            # a non-ASCII key into the env var -- Fernet keys are
            # always URL-safe base64 so any non-ASCII is invalid by
            # definition.
            msg = f"Invalid Fernet key in {env_var}"
            raise MasterKeyError(msg) from exc

    async def store(
        self,
        secret_id: NotBlankStr,
        value: bytes,
    ) -> None:
        """Encrypt and store a secret.

        ``store`` is idempotent via UPSERT: if a row with the same
        ``secret_id`` already exists, its ciphertext and
        ``rotated_at`` are overwritten. Callers that need to detect
        overwrites must read first.

        Raises:
            SecretStorageError: If the secret store rejects the write.
            MasterKeyError: If the configured master key is missing or invalid.
        """
        try:
            encrypted = self._fernet.encrypt(value)
            pool = self._get_pool()
            async with pool.connection() as conn, conn.cursor() as cur:
                # Fernet uses a fresh IV on every call, so the raw
                # ciphertext differs between back-to-back ``store``
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
                    (secret_id, encrypted),
                )
            logger.debug(SECRET_STORED, secret_id=secret_id)
        except MasterKeyError:
            raise
        except psycopg.Error as exc:
            # Demote to ``warning`` + scrubbed description so the
            # driver's ciphertext-bearing locals do not end up in
            # the traceback frame. Mirrors ``encrypted_sqlite.py``.
            logger.warning(
                SECRET_STORAGE_FAILED,
                secret_id=secret_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to store secret {secret_id}"
            raise SecretStorageError(msg) from exc

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        """Retrieve and decrypt a secret.

        Returns:
            The matching value, or ``None`` when absent.

        Raises:
            SecretRetrievalError: If decryption fails or the row is unreadable.
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

        try:
            return self._fernet.decrypt(bytes(row[0]))
        except InvalidToken as exc:
            logger.warning(
                SECRET_RETRIEVAL_FAILED,
                secret_id=secret_id,
                error_type="InvalidToken",
                error="wrong key or corrupted data",
            )
            msg = f"Failed to decrypt secret {secret_id}"
            raise SecretRetrievalError(msg) from exc
        except (ValueError, TypeError) as exc:
            # Narrow catch for residual decrypt-path failures: ValueError
            # / TypeError from cryptography's internals (e.g., if row[0]
            # is None due to a schema drift) or from bytes() coercion of
            # an unexpected column type. InvalidToken is handled above;
            # anything else is a contract violation worth seeing.
            logger.warning(
                SECRET_RETRIEVAL_FAILED,
                secret_id=secret_id,
                error_type=type(exc).__name__,
                error=f"decrypt failed: {type(exc).__name__}",
            )
            msg = f"Failed to decrypt secret {secret_id}"
            raise SecretRetrievalError(msg) from exc

    async def delete(self, secret_id: NotBlankStr) -> bool:
        """Delete a secret.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

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

    async def rotate(
        self,
        old_id: NotBlankStr,
        new_value: bytes,
    ) -> NotBlankStr:
        """Rotate: store new value under new ID, delete old.

        If deletion of ``old_id`` fails after ``new_id`` has been
        written, the new secret is deleted as a best-effort rollback
        so callers are never left referencing a half-committed
        rotation. Rollback failures are embedded in the raised
        ``SecretRotationError`` for manual cleanup.

        Returns:
            Result of type ``NotBlankStr``.
        """
        new_id = str(uuid4())
        await self._rotate_store_new(old_id, new_id, new_value)
        await self._rotate_delete_old(old_id, new_id)
        logger.info(SECRET_ROTATED, old_id=old_id, new_id=new_id)
        return new_id

    async def _rotate_store_new(
        self,
        old_id: NotBlankStr,
        new_id: NotBlankStr,
        new_value: bytes,
    ) -> None:
        """Write the new secret during rotation, wrapping failures.

        Raises:
            SecretRotationError: If rotation cannot complete cleanly.
        """
        try:
            await self.store(new_id, new_value)
        except (SecretStorageError, MasterKeyError) as exc:
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                old_id=old_id,
                error_type=type(exc).__name__,
                error=f"store of new secret failed: {safe_error_description(exc)}",
            )
            msg = f"Failed to store rotated secret (old_id={old_id})"
            raise SecretRotationError(msg) from exc

    async def _rotate_delete_old(
        self,
        old_id: NotBlankStr,
        new_id: NotBlankStr,
    ) -> None:
        """Delete the old secret during rotation, rolling back new on failure.

        Raises:
            SecretRotationError: If rotation cannot complete cleanly.
        """
        try:
            deleted = await self.delete(old_id)
        except SecretStorageError as exc:
            rollback_note = await self._rollback_new(new_id)
            scrubbed = safe_error_description(exc)
            detail = (
                f"delete of old secret failed: {scrubbed}; rollback: {rollback_note}"
            )
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                old_id=old_id,
                new_id=new_id,
                error_type=type(exc).__name__,
                error=detail,
            )
            msg = (
                f"Failed to delete old secret {old_id} during rotation; {rollback_note}"
            )
            raise SecretRotationError(msg) from exc

        if not deleted:
            rollback_note = await self._rollback_new(new_id)
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                old_id=old_id,
                new_id=new_id,
                error=(
                    f"old secret not found at delete time; rollback: {rollback_note}"
                ),
            )
            msg = f"Old secret {old_id} not found during rotation; {rollback_note}"
            raise SecretRotationError(msg)

    async def _rollback_new(self, new_id: NotBlankStr) -> str:
        """Attempt to delete *new_id* after a failed rotation.

        Returns:
            Result of type ``str``.
        """
        try:
            await self.delete(new_id)
        except SecretStorageError as rb_exc:
            # Wrap the scrub + return-string construction so a
            # broken ``__str__`` on the rollback error cannot crash
            # the rotation path silently. Re-raise catastrophic
            # interpreter state (``MemoryError`` /
            # ``RecursionError``) so the process surfaces the
            # failure.
            try:
                scrubbed = safe_error_description(rb_exc)
            except Exception as exc:  # pragma: no cover - defensive
                _reraise_critical(exc)
                scrubbed = type(rb_exc).__name__
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                new_id=new_id,
                error_type=type(rb_exc).__name__,
                error=f"rollback delete failed: {scrubbed}",
            )
            return f"rollback of new_id={new_id} also failed: {scrubbed}"
        return f"new_id={new_id} rolled back"

    async def close(self) -> None:
        """No-op: the pool is owned by the main persistence backend."""
