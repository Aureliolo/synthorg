"""Secret backend protocol.

All secret backends implement this protocol so that application
code depends only on the abstract interface, never on a specific
storage engine.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class SecretBackend(Protocol):
    """Encrypted credential storage backend.

    Secrets are stored as opaque byte blobs.  The backend handles
    encryption/decryption transparently.
    """

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        ...

    async def store(
        self,
        secret_id: NotBlankStr,
        value: bytes,
    ) -> None:
        """Store an encrypted secret.

        Raises:
            SecretStorageError: On write failure.
        """
        ...

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        """Retrieve and decrypt a secret.

        Returns:
            Decrypted bytes, or ``None`` if the secret does not exist.

        Raises:
            SecretRetrievalError: On read failure.
        """
        ...

    async def delete(self, secret_id: NotBlankStr) -> bool:
        """Delete a secret.

        Returns:
            ``True`` if the secret existed and was deleted.

        Raises:
            SecretStorageError: On delete failure.
        """
        ...

    async def rotate(
        self,
        old_id: NotBlankStr,
        new_value: bytes,
    ) -> NotBlankStr:
        """Rotate a secret: store *new_value* under a new ID, delete *old_id*.

        Returns:
            The new secret ID.

        Raises:
            SecretRotationError: If rotation fails.
        """
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...
