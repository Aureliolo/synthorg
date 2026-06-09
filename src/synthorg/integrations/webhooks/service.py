"""WebhookService -- facade for ``synthorg_webhooks_*`` MCP tools."""

from collections.abc import Sequence
from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.integrations.webhooks.definition_store_protocol import (
    WebhookDefinitionStore,
)
from synthorg.integrations.webhooks.models import WebhookDefinition
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMMUNICATION_WEBHOOK_CREATED,
    COMMUNICATION_WEBHOOK_DELETED,
    COMMUNICATION_WEBHOOK_UPDATED,
)

logger = get_logger(__name__)


class WebhookService:
    """Facade wrapping :class:`WebhookDefinitionStore`.

    Args:
        store: The backing definition store.
    """

    def __init__(self, *, store: WebhookDefinitionStore) -> None:
        self._store = store

    async def list_webhooks(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[Sequence[WebhookDefinition], int]:
        """Return paginated definitions newest-first + unfiltered total.

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
        definitions = tuple(await self._store.list_definitions())
        total = len(definitions)
        end = total if limit is None else offset + limit
        return (definitions[offset:end], total)

    async def get_webhook(
        self,
        definition_id: NotBlankStr,
    ) -> WebhookDefinition | None:
        """Return one definition by ID."""
        return await self._store.get_by_id(definition_id)

    async def create_webhook(
        self,
        *,
        definition: WebhookDefinition,
        actor_id: NotBlankStr,
    ) -> WebhookDefinition:
        """Persist a new definition.

        Returns:
            The same ``WebhookDefinition`` after it has been persisted.
        """
        await self._store.add(definition)
        logger.info(
            COMMUNICATION_WEBHOOK_CREATED,
            webhook_id=str(definition.id),
            webhook_name=definition.name,
            actor_id=actor_id,
        )
        return definition

    async def update_webhook(
        self,
        *,
        definition: WebhookDefinition,
        actor_id: NotBlankStr,
    ) -> WebhookDefinition:
        """Replace an existing definition (by ID).

        Returns:
            A copy of the definition with ``updated_at`` refreshed to the
            current UTC time, after the store replace succeeds.
        """
        refreshed = definition.model_copy(
            update={"updated_at": datetime.now(UTC)},
        )
        await self._store.replace(refreshed)
        logger.info(
            COMMUNICATION_WEBHOOK_UPDATED,
            webhook_id=str(refreshed.id),
            webhook_name=refreshed.name,
            actor_id=actor_id,
        )
        return refreshed

    async def delete_webhook(
        self,
        *,
        definition_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove a definition; returns ``True`` when one was removed.

        The audit event is only emitted when something was actually
        deleted; a miss returns ``False`` without logging a destructive
        operation that never happened.

        Returns:
            ``True`` when a definition was removed; ``False`` on a miss.
        """
        removed = await self._store.delete(definition_id)
        if not removed:
            return False
        logger.info(
            COMMUNICATION_WEBHOOK_DELETED,
            webhook_id=definition_id,
            actor_id=actor_id,
            reason=reason,
            removed=removed,
        )
        return removed


__all__ = [
    "WebhookService",
]
