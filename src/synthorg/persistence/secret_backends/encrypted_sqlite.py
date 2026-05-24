"""Fernet-encrypted SQLite secret backend.

Secrets are encrypted with a Fernet key derived from the
``SYNTHORG_MASTER_KEY`` environment variable and stored in the
``connection_secrets`` table of the persistence database.
"""

import os
from uuid import uuid4

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from synthorg.core.critical_errors import _reraise_critical
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.config import EncryptedSqliteConfig
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

logger = get_logger(__name__)


class EncryptedSqliteSecretBackend:
    """Fernet-encrypted SQLite secret backend.

    Secrets are stored as encrypted blobs in the ``connection_secrets``
    table.  The Fernet key is read from the environment variable
    specified in ``config.master_key_env`` (default
    ``SYNTHORG_MASTER_KEY``).

    If the env var is not set, a new key is generated and a
    ``MasterKeyError`` is raised with instructions.

    Args:
        db_path: Path to the SQLite database file.
        config: Encrypted SQLite backend configuration.
    """

    def __init__(
        self,
        db_path: str,
        config: EncryptedSqliteConfig | None = None,
    ) -> None:
        cfg = config or EncryptedSqliteConfig()
        self._db_path = db_path
        self._fernet = self._init_fernet(cfg.master_key_env)

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return "encrypted_sqlite"

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
            # Never include a generated key in the error text: the
            # message may be captured by log aggregators or error
            # trackers, and any Fernet key in that payload becomes
            # a valid decryption key for everything stored later.
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
            msg = f"Invalid Fernet key in {env_var}"
            raise MasterKeyError(msg) from exc

    async def store(
        self,
        secret_id: NotBlankStr,
        value: bytes,
    ) -> None:
        """Encrypt and store a secret.

        ``store`` is idempotent via ``INSERT OR REPLACE``: if a row
        with the same ``secret_id`` already exists, its ciphertext is
        overwritten. Callers that need to detect overwrites must read
        first.

        Raises:
            SecretStorageError: If the secret store rejects the write.
            MasterKeyError: If the configured master key is missing or invalid.
        """
        try:
            encrypted = self._fernet.encrypt(value)
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO connection_secrets "
                    "(secret_id, encrypted_value, key_version, "
                    "created_at, rotated_at) "
                    "VALUES (?, ?, 1, datetime('now'), NULL)",
                    (secret_id, encrypted),
                )
                await db.commit()
            logger.debug(SECRET_STORED, secret_id=secret_id)
        except MasterKeyError:
            raise
        except Exception as exc:
            _reraise_critical(exc)
            # ``warning`` + scrubbed description: driver exceptions may
            # embed connection URIs or raw Fernet ciphertext bytes.
            # Traceback attachment via ``logger.exception`` would also
            # serialize the request-level ``value`` parameter we just
            # encrypted. Scrubbed type+message is enough for triage;
            # ``raise ... from exc`` preserves the chain for callers.
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
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT encrypted_value FROM connection_secrets "
                    "WHERE secret_id = ?",
                    (secret_id,),
                )
                row = await cursor.fetchone()
        except Exception as exc:
            _reraise_critical(exc)
            # ``warning`` + scrubbed description: same rationale as
            # ``store()`` above. A DB driver failure may embed the row's
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

        try:
            return self._fernet.decrypt(row[0])
        except InvalidToken as exc:
            # Decrypt failure: the ciphertext bytes (``row[0]``) are in
            # the local frame, so ``logger.exception`` would serialize
            # them via traceback. Static category string keeps triage
            # useful without leaking key material.
            logger.warning(
                SECRET_RETRIEVAL_FAILED,
                secret_id=secret_id,
                error_type="InvalidToken",
                error="wrong key or corrupted data",
            )
            msg = f"Failed to decrypt secret {secret_id}"
            raise SecretRetrievalError(msg) from exc
        except Exception as exc:
            _reraise_critical(exc)
            # Catch-all so any residual decrypt failure (malformed
            # row data, driver bug, etc.) still surfaces through
            # the secret-backend contract instead of leaking raw.
            # Same traceback concern as the ``InvalidToken`` branch.
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
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM connection_secrets WHERE secret_id = ?",
                    (secret_id,),
                )
                await db.commit()
                deleted = cursor.rowcount > 0
        except Exception as exc:
            _reraise_critical(exc)
            # Driver exceptions may embed connection URIs with
            # credentials; scrub + drop traceback.  Use the
            # ``SECRET_DELETE_FAILED`` event (not ``STORAGE_FAILED``)
            # so delete failures stay distinguishable from writes,
            # matching the Postgres backend.
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
        written, the new secret is deleted as a best-effort
        rollback so callers are never left referencing a half-
        committed rotation. Rollback failures are embedded in the
        raised ``SecretRotationError`` so the caller can take
        manual cleanup action if needed.

        Returns:
            Result of type ``NotBlankStr``.

        Raises:
            SecretRotationError: If rotation cannot complete cleanly.
        """
        new_id = str(uuid4())
        try:
            await self.store(new_id, new_value)
        except Exception as exc:
            _reraise_critical(exc)
            # Carry the scrubbed description as context; the underlying
            # exception stays in the chain via ``raise ... from exc``.
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                old_id=old_id,
                error_type=type(exc).__name__,
                error=f"store of new secret failed: {safe_error_description(exc)}",
            )
            msg = f"Failed to store rotated secret (old_id={old_id})"
            raise SecretRotationError(msg) from exc

        try:
            deleted = await self.delete(old_id)
        except Exception as exc:
            _reraise_critical(exc)
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                old_id=old_id,
                new_id=new_id,
                error_type=type(exc).__name__,
                error=f"delete of old secret failed: {safe_error_description(exc)}",
            )
            rollback_note = await self._rollback_new(new_id)
            msg = (
                f"Failed to delete old secret {old_id} during rotation; {rollback_note}"
            )
            raise SecretRotationError(msg) from exc

        if not deleted:
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                old_id=old_id,
                new_id=new_id,
                error="old secret not found at delete time",
            )
            rollback_note = await self._rollback_new(new_id)
            msg = f"Old secret {old_id} not found during rotation; {rollback_note}"
            raise SecretRotationError(msg)

        logger.info(
            SECRET_ROTATED,
            old_id=old_id,
            new_id=new_id,
        )
        return new_id

    async def _rollback_new(self, new_id: NotBlankStr) -> str:
        """Attempt to delete *new_id* after a failed rotation.

        Returns:
            Result of type ``str``.
        """
        try:
            await self.delete(new_id)
        except Exception as rb_exc:
            _reraise_critical(rb_exc)
            # Wrap the scrub so a broken ``__str__`` on the rollback
            # error cannot crash the rotation path silently.
            try:
                scrubbed = safe_error_description(rb_exc)
            except Exception as scrub_exc:  # pragma: no cover - defensive
                _reraise_critical(scrub_exc)
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
        """No-op for SQLite (connections are per-call)."""
