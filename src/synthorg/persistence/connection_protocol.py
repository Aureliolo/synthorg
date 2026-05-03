"""Repository protocols for integration persistence.

Defines CRUD interfaces for connections, encrypted secret blobs, OAuth
authorization states, and webhook receipts.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.connections.models import (
    Connection,  # noqa: TC001
    ConnectionType,  # noqa: TC001
    OAuthState,  # noqa: TC001
    WebhookReceipt,  # noqa: TC001
)


@runtime_checkable
class ConnectionRepository(Protocol):
    """CRUD + query interface for Connection persistence."""

    async def save(self, connection: Connection) -> None:
        """Persist a connection (insert or upsert)."""
        ...

    async def get(self, name: NotBlankStr) -> Connection | None:
        """Retrieve a connection by name."""
        ...

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List all connections, optionally bounded by *limit* / *offset*.

        Sorted by ``name`` ascending; pass ``limit=None`` (default) to
        retain the legacy fetch-all semantics, or set both to consume
        a paginated window. ``limit <= 0`` returns ``()``; negative
        ``offset`` is treated as ``0``.
        """
        ...

    async def list_by_type(
        self,
        connection_type: ConnectionType,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List connections of a specific type with optional limit/offset.

        ``limit <= 0`` returns ``()``; negative ``offset`` is treated as ``0``.
        """
        ...

    async def delete(self, name: NotBlankStr) -> bool:
        """Delete a connection by name.

        Returns:
            ``True`` if the connection existed and was deleted.
        """
        ...


@runtime_checkable
class ConnectionSecretRepository(Protocol):
    """Low-level CRUD for encrypted secret blobs.

    Used by ``EncryptedSqliteSecretBackend``; other backends
    manage their own storage.
    """

    async def store(
        self,
        secret_id: NotBlankStr,
        encrypted_value: bytes,
        key_version: int,
    ) -> None:
        """Persist an encrypted secret."""
        ...

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        """Retrieve an encrypted secret blob."""
        ...

    async def delete(self, secret_id: NotBlankStr) -> bool:
        """Delete an encrypted secret."""
        ...


@runtime_checkable
class OAuthStateRepository(Protocol):
    """CRUD for transient OAuth authorization states."""

    async def save(self, state: OAuthState) -> None:
        """Persist an OAuth state."""
        ...

    async def get(self, state_token: NotBlankStr) -> OAuthState | None:
        """Retrieve by state token."""
        ...

    async def delete(self, state_token: NotBlankStr) -> bool:
        """Delete a state token (consumed or expired)."""
        ...

    async def cleanup_expired(self) -> int:
        """Delete all expired states.

        Returns:
            Number of deleted rows.
        """
        ...


@runtime_checkable
class WebhookReceiptRepository(Protocol):
    """CRUD for webhook receipt log entries."""

    async def log(self, receipt: WebhookReceipt) -> None:
        """Persist a webhook receipt."""
        ...

    async def get_by_connection(
        self,
        connection_name: NotBlankStr,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WebhookReceipt, ...]:
        """List receipts for a connection, newest first.

        ``limit <= 0`` returns ``()``; ``offset`` skips that many rows
        before slicing the limit window.
        """
        ...

    async def cleanup_old_for_connection(
        self,
        connection_name: NotBlankStr,
        retention_days: int,
    ) -> int:
        """Delete receipts for *connection_name* older than *retention_days*.

        ``retention_days <= 0`` is a no-op so callers cannot accidentally
        truncate a connection's log via misconfiguration.

        Returns:
            Number of deleted rows.
        """
        ...
