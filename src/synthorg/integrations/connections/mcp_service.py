"""ConnectionService -- MCP facade over :class:`ConnectionCatalog`.

Thin pass-through wrapper that exposes the catalog's CRUD + health
surface through a typed service facade so MCP handlers stay parse/
dispatch/wrap.  All mutations are audit-logged on the facade (single
owner of audit logging, per the persistence-boundary convention).
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMMUNICATION_CONNECTION_CREATED,
    COMMUNICATION_CONNECTION_DELETED,
    COMMUNICATION_CONNECTION_HEALTH_CHECKED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.connections.models import Connection, ConnectionType

logger = get_logger(__name__)


class ConnectionService:
    """Facade wrapping :class:`ConnectionCatalog` for the MCP surface.

    Args:
        catalog: The connection catalog (already on ``AppState``).
    """

    def __init__(self, *, catalog: ConnectionCatalog) -> None:
        self._catalog = catalog

    async def list_connections(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[Sequence[Connection], int]:
        """Return paginated connections and the unfiltered total.

        Raises:
            ValueError: If ``offset`` is negative, or if ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        connections = tuple(await self._catalog.list_all())
        total = len(connections)
        end = total if limit is None else offset + limit
        return (connections[offset:end], total)

    async def get_connection(
        self,
        name: NotBlankStr,
    ) -> Connection | None:
        """Return a connection by name or ``None``."""
        return await self._catalog.get(name)

    async def create_connection(  # noqa: PLR0913
        self,
        *,
        name: NotBlankStr,
        connection_type: ConnectionType,
        auth_method: NotBlankStr,
        credentials: dict[str, str],
        actor_id: NotBlankStr,
        base_url: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Connection:
        """Create and return a new connection, auditing the event.

        Returns:
            The newly created and persisted ``Connection``.
        """
        connection = await self._catalog.create(
            name=name,
            connection_type=connection_type,
            auth_method=auth_method,
            credentials=credentials,
            base_url=base_url,
            metadata=metadata,
        )
        logger.info(
            COMMUNICATION_CONNECTION_CREATED,
            connection_name=name,
            connection_type=str(connection_type),
            actor_id=actor_id,
        )
        return connection

    async def delete_connection(
        self,
        *,
        name: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> None:
        """Delete a connection, auditing the event on actual removal.

        The underlying :meth:`ConnectionCatalog.delete` raises
        :class:`ConnectionNotFoundError` when the connection does not
        exist, so the audit event is only emitted when a row was
        actually removed -- the ``await`` below re-raises the miss
        before reaching the ``logger.info`` call.
        """
        await self._catalog.delete(name)
        logger.info(
            COMMUNICATION_CONNECTION_DELETED,
            connection_name=name,
            actor_id=actor_id,
            reason=reason,
        )

    async def check_health(
        self,
        *,
        name: NotBlankStr,
    ) -> Connection | None:
        """Probe the catalog for the latest health status.

        Returns:
            The ``Connection`` with its last-known health status, or
            ``None`` when the connection is not in the catalog.
        """
        connection = await self._catalog.get(name)
        logger.info(
            COMMUNICATION_CONNECTION_HEALTH_CHECKED,
            connection_name=name,
            found=connection is not None,
        )
        return connection


__all__ = [
    "ConnectionService",
]
