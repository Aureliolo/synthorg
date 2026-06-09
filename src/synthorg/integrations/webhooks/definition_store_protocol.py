"""Protocol for the webhook-definition store.

The store holds operator-managed :class:`WebhookDefinition` records.
MCP handlers (``synthorg_webhooks_*``) route through a facade which
delegates to this protocol; the in-memory implementation is the
default for dev/test and can be swapped for a durable backend later.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.integrations.webhooks.models import WebhookDefinition


@runtime_checkable
class WebhookDefinitionStore(Protocol):
    """CRUD + lifecycle for webhook definitions."""

    async def list_definitions(self) -> tuple[WebhookDefinition, ...]:
        """Return all definitions ordered newest-first by ``created_at``."""
        ...

    async def get_by_id(
        self,
        definition_id: NotBlankStr,
    ) -> WebhookDefinition | None:
        """Return one definition by ID, or ``None`` when absent."""
        ...

    async def get_by_name(
        self,
        name: NotBlankStr,
    ) -> WebhookDefinition | None:
        """Return one definition by unique name, or ``None``."""
        ...

    async def add(self, definition: WebhookDefinition) -> None:
        """Persist a new definition.

        Raises:
            ValueError: If a definition with the same name already
                exists.
        """
        ...

    async def replace(self, definition: WebhookDefinition) -> None:
        """Replace an existing definition (by ID).

        Raises:
            KeyError: If no definition with the given ID exists.
            ValueError: If another definition with the same name
                already exists under a different ID.
        """
        ...

    async def delete(self, definition_id: NotBlankStr) -> bool:
        """Remove a definition; returns ``True`` when one was removed."""
        ...

    async def clear(self) -> None:
        """Drop all definitions.  Intended for tests."""
        ...
