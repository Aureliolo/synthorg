"""Backend-specific row I/O seam for encrypted secret storage.

The :class:`SecretRowStore` is the ONLY surface that differs between the
SQLite and Postgres encrypted secret backends: the driver, the SQL
dialect (``?`` vs ``%s`` placeholders, ``INSERT OR REPLACE`` vs
``ON CONFLICT ... DO UPDATE``), and the driver-error type caught. Fernet
encryption, key handling, rotation, and the success/failure log events
all live once in
:class:`~synthorg.persistence.secret_backends.encrypted.EncryptedSecretBackend`,
which composes a row store.

Row stores deal only in already-encrypted ciphertext blobs; they never
see plaintext. Each method that touches the driver logs the scrubbed
failure (event + ``error_type`` + ``safe_error_description``) and raises
the matching secret-backend error, so the composing backend can stay
driver-agnostic.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class SecretRowStore(Protocol):
    """Persist opaque ciphertext rows keyed by secret id.

    Implementations own a single connection / pool so secret material
    stays isolated from the main application data plane.
    """

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        ...

    async def upsert(self, secret_id: NotBlankStr, ciphertext: bytes) -> None:
        """Insert or replace the ciphertext row for *secret_id*.

        Args:
            secret_id: Identifier of the secret.
            ciphertext: Fernet-encrypted value to store.

        Raises:
            SecretStorageError: On write failure (already logged scrubbed).
        """
        ...

    async def fetch(self, secret_id: NotBlankStr) -> bytes | None:
        """Read the ciphertext row for *secret_id*.

        Args:
            secret_id: Identifier of the secret.

        Returns:
            The stored ciphertext bytes, or ``None`` when absent.

        Raises:
            SecretRetrievalError: On read failure (already logged scrubbed).
        """
        ...

    async def delete(self, secret_id: NotBlankStr) -> bool:
        """Delete the ciphertext row for *secret_id*.

        Args:
            secret_id: Identifier of the secret.

        Returns:
            ``True`` when a row was deleted, ``False`` if none existed.

        Raises:
            SecretStorageError: On delete failure (already logged scrubbed).
        """
        ...

    async def close(self) -> None:
        """Release any resources owned by the row store."""
        ...
