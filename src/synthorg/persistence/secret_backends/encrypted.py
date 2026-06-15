"""Fernet-encrypted secret backend over a pluggable row store.

Holds everything that is identical across storage engines: the
master-key Fernet initialisation, the encrypt/decrypt boundary, secret
id minting, rotation-with-rollback, and the success/failure log events.
The only backend-specific surface -- raw ciphertext row I/O and its
driver-error translation -- lives behind the injected
:class:`~synthorg.persistence.secret_backends.row_store.SecretRowStore`
(``SqliteSecretRowStore`` or ``PostgresSecretRowStore``), so SQLite and
Postgres secret storage stay in lockstep by construction.

Secrets are encrypted with a Fernet key derived from the environment
variable named by the backend config (default ``SYNTHORG_MASTER_KEY``).
If the env var is not set, a ``MasterKeyError`` is raised with
instructions for generating a key.
"""

import asyncio
import contextlib
import os
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import (
    MasterKeyError,
    SecretRetrievalError,
    SecretRotationError,
    SecretStorageError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    SECRET_BACKEND_UNAVAILABLE,
    SECRET_RETRIEVAL_FAILED,
    SECRET_ROTATED,
    SECRET_STORED,
)
from synthorg.persistence.secret_backends.row_store import SecretRowStore

logger = get_logger(__name__)


class EncryptedSecretBackend:
    """Fernet-encrypted secret backend composing a row store.

    Args:
        store: The backend-specific ciphertext row store.
        master_key_env: Name of the environment variable holding the
            Fernet master key.
    """

    def __init__(self, store: SecretRowStore, *, master_key_env: str) -> None:
        self._store = store
        self._fernet = self._init_fernet(master_key_env)

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return self._store.backend_name

    @staticmethod
    def _init_fernet(env_var: str) -> Fernet:
        """Load the Fernet master key from the environment.

        Returns:
            Result of type ``Fernet``.

        Raises:
            MasterKeyError: If the configured master key is missing or
                invalid.
        """
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            # Never include a generated key in the error text: the
            # message may be captured by log aggregators or error
            # trackers, and any Fernet key in that payload becomes a
            # valid decryption key for everything stored later.
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
            # UnicodeEncodeError defends against accidentally pasting a
            # non-ASCII key into the env var -- Fernet keys are always
            # URL-safe base64 so any non-ASCII is invalid by definition.
            msg = f"Invalid Fernet key in {env_var}"
            raise MasterKeyError(msg) from exc

    async def store(
        self,
        secret_id: NotBlankStr,
        value: bytes,
    ) -> None:
        """Encrypt and store a secret.

        ``store`` is idempotent via the row store's upsert: if a row
        with the same ``secret_id`` already exists, its ciphertext is
        overwritten. Callers that need to detect overwrites must read
        first.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        encrypted = self._fernet.encrypt(value)
        await self._store.upsert(secret_id, encrypted)
        logger.debug(SECRET_STORED, secret_id=secret_id)

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        """Retrieve and decrypt a secret.

        Returns:
            The matching value, or ``None`` when absent.

        Raises:
            SecretRetrievalError: If decryption fails or the row is
                unreadable.
        """
        ciphertext = await self._store.fetch(secret_id)
        if ciphertext is None:
            return None

        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            # Decrypt failure: the ciphertext bytes are in the local
            # frame, so ``logger.exception`` would serialize them via
            # traceback. Static category string keeps triage useful
            # without leaking key material.
            logger.warning(
                SECRET_RETRIEVAL_FAILED,
                secret_id=secret_id,
                error_type="InvalidToken",
                error="wrong key or corrupted data",
            )
            msg = f"Failed to decrypt secret {secret_id}"
            raise SecretRetrievalError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            # Catch-all so any residual decrypt failure (malformed row
            # data, driver bug, etc.) still surfaces through the
            # secret-backend contract instead of leaking raw. Same
            # traceback concern as the ``InvalidToken`` branch.
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
            ``True`` when a row was deleted, ``False`` if none existed.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        return await self._store.delete(secret_id)

    async def rotate(
        self,
        old_id: NotBlankStr,
        new_value: bytes,
    ) -> NotBlankStr:
        """Rotate: store new value under new ID, delete old.

        If deletion of ``old_id`` fails after ``new_id`` has been
        written, the new secret is deleted as a best-effort rollback so
        callers are never left referencing a half-committed rotation.
        Rollback failures are embedded in the raised
        ``SecretRotationError`` for manual cleanup.

        Returns:
            Result of type ``NotBlankStr``.

        Raises:
            SecretRotationError: If rotation cannot complete cleanly.
            asyncio.CancelledError: Re-raised after a best-effort rollback
                of the newly written secret.
        """
        new_id = str(uuid4())
        try:
            await self._rotate_store_new(old_id, new_id, new_value)
        except asyncio.CancelledError:
            # Cancellation delivered as the new secret is written can still
            # commit new_id before unwinding; roll it back with the same
            # shielded best-effort delete as the delete-old phase below.
            with contextlib.suppress(SecretStorageError):
                await asyncio.shield(self.delete(new_id))
            raise
        try:
            await self._rotate_delete_old(old_id, new_id)
        except asyncio.CancelledError:
            # Cancellation after new_id is written but before old_id is
            # deleted would orphan new_id. Best-effort shielded rollback so
            # a cancelled rotation never leaves an unreferenced secret.
            with contextlib.suppress(SecretStorageError):
                await asyncio.shield(self.delete(new_id))
            raise
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
        except SecretStorageError as exc:
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
            scrubbed = safe_error_description(rb_exc)
            logger.warning(
                SECRET_BACKEND_UNAVAILABLE,
                new_id=new_id,
                error_type=type(rb_exc).__name__,
                error=f"rollback delete failed: {scrubbed}",
            )
            return f"rollback of new_id={new_id} also failed: {scrubbed}"
        return f"new_id={new_id} rolled back"

    async def close(self) -> None:
        """Release backend resources via the row store."""
        await self._store.close()
