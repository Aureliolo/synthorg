"""Repository protocols for integration persistence.

Defines CRUD interfaces for connections, encrypted secret blobs, OAuth
authorization states, and webhook receipts.
"""

from datetime import datetime  # noqa: TC003 -- runtime annotation
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionType,
    OAuthState,
    WebhookReceipt,
)
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class ConnectionFilterSpec(BaseModel):
    """Filter spec for ``ConnectionRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    connection_type: ConnectionType | None = Field(
        default=None,
        description="Filter by connection type",
    )


@runtime_checkable
class ConnectionRepository(
    IdKeyedRepository[Connection, NotBlankStr],
    FilteredQueryRepository[Connection, ConnectionFilterSpec],
    Protocol,
):
    """CRUD + query interface for Connection persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Entity is keyed by ``name`` field.
    """

    async def save(self, entity: Connection) -> None:
        """Persist a connection (insert or upsert by name)."""
        ...

    async def get(self, entity_id: NotBlankStr) -> Connection | None:
        """Retrieve a connection by name."""
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List all connections with pagination.

        Sorted by ``name`` ascending.
        """
        ...

    async def query(
        self,
        filter_spec: ConnectionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List connections matching the filter spec.

        Sorted by ``name`` ascending.
        """
        ...

    async def count(self, filter_spec: ConnectionFilterSpec) -> int:
        """Count connections matching the filter spec."""
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
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
class OAuthStateRepository(
    IdKeyedRepository[OAuthState, NotBlankStr],
    Protocol,
):
    """CRUD for transient OAuth authorization states.

    Composes :class:`IdKeyedRepository` (ADR-0001). Entity is keyed by
    ``state_token`` field. Bespoke per ADR-0001 D7: :meth:`mark_consumed`
    (compare-and-set for idempotency) and :meth:`cleanup_expired` (TTL-based
    garbage collection).
    """

    async def save(self, entity: OAuthState) -> None:
        """Persist an OAuth state."""
        ...

    async def get(self, entity_id: NotBlankStr) -> OAuthState | None:
        """Retrieve by state token."""
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OAuthState, ...]:
        """List all OAuth states with pagination."""
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a state token (consumed or expired)."""
        ...

    async def mark_consumed(
        self,
        state_token: NotBlankStr,
        *,
        connection_name: NotBlankStr,
        consumed_at: datetime,
    ) -> bool:
        """Mark a state token as consumed by a successful callback.

        Stamps ``consumed_at`` and records ``connection_name`` so a
        redelivered callback (provider retry, browser back-button,
        CDN replay) returns the original connection name without
        re-exchanging the authorization code. The compare-and-set is
        atomic at the row level; second and subsequent calls observe
        the existing ``consumed_at`` and return ``False``.

        Implementations MUST stamp both ``consumed_at`` and
        ``connection_name_returned`` in a single atomic UPDATE.
        :class:`OAuthState` validates the two fields are always set
        together (see ``_validate_consumed_pair``); a partial write
        would let a redelivered callback observe ``consumed_at`` set
        with no ``connection_name_returned`` to return.

        Returns:
            ``True`` if a row was updated (state existed and was not
            already consumed); ``False`` if the row was missing or
            already consumed.

        Raises:
            QueryError: On database errors.
        """
        ...

    async def cleanup_expired(self, retention_seconds: float) -> int:
        """Delete all expired states.

        Also reaps consumed-but-stale rows older than
        ``retention_seconds`` so the idempotency table does not grow
        unbounded.

        Returns:
            Number of deleted rows.
        """
        ...


@runtime_checkable
class WebhookReceiptRepository(
    IdKeyedRepository[WebhookReceipt, NotBlankStr],
    Protocol,
):
    """CRUD for webhook receipt log entries.

    Composes :class:`IdKeyedRepository` (ADR-0001). Entity is keyed by
    ``receipt_id`` field. Bespoke per ADR-0001 D7: :meth:`update_status`
    and :meth:`update_status_if_current` (lifecycle updates), :meth:`get_by_connection`
    (alternate-key query), and :meth:`cleanup_old_for_connection` (retention
    policy).
    """

    async def save(self, entity: WebhookReceipt) -> None:
        """Persist a webhook receipt."""
        ...

    async def get(self, entity_id: NotBlankStr) -> WebhookReceipt | None:
        """Fetch a single receipt by ID, or ``None`` when absent.

        Used by the retry endpoint to look up a failed receipt before
        re-publishing its captured payload to the bus.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WebhookReceipt, ...]:
        """List all webhook receipts with pagination."""
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a webhook receipt by ID.

        Returns:
            ``True`` if the receipt existed and was deleted.
        """
        ...

    async def update_status(
        self,
        receipt_id: NotBlankStr,
        *,
        status: str,
        processed_at: datetime | None,
        error: str | None,
    ) -> bool:
        """Update the receipt's lifecycle fields.

        Returns ``True`` when the row existed and was updated, ``False``
        when no row matched the ID. Callers can use the boolean to
        distinguish "not found" from a successful no-op without raising.
        """
        ...

    async def update_status_if_current(
        self,
        receipt_id: NotBlankStr,
        *,
        expected_status: str,
        status: str,
        processed_at: datetime | None,
        error: str | None,
    ) -> bool:
        """Compare-and-set variant of ``update_status``.

        Atomically updates the row only when its current ``status`` column
        equals ``expected_status``. Returns ``True`` on a successful
        transition, ``False`` when the row is missing OR the row's
        current status differs from ``expected_status`` (lost the race).
        The retry endpoint uses this to close the TOCTOU window where
        two concurrent operator-triggered retries could both load the
        same receipt, both transition it to ``retrying``, and both
        republish the captured payload.
        """
        ...

    async def get_by_connection(
        self,
        connection_name: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
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
