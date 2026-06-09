"""In-memory webhook-definition store."""

import asyncio
from uuid import UUID

from synthorg.core.types import NotBlankStr
from synthorg.integrations.webhooks.models import WebhookDefinition
from synthorg.observability import get_logger

logger = get_logger(__name__)


class InMemoryWebhookDefinitionStore:
    """Process-local dict store for :class:`WebhookDefinition`.

    Uniqueness is enforced on ``name``; lookups can use either ID or
    name.  A single ``asyncio.Lock`` serialises mutations so read-
    then-write sequences stay atomic under concurrent callers.
    """

    def __init__(self) -> None:
        self._by_id: dict[UUID, WebhookDefinition] = {}
        self._lock = asyncio.Lock()

    async def list_definitions(self) -> tuple[WebhookDefinition, ...]:
        """Return all definitions ordered newest-first."""
        async with self._lock:
            snapshot = tuple(self._by_id.values())
        return tuple(sorted(snapshot, key=lambda d: d.created_at, reverse=True))

    async def get_by_id(
        self,
        definition_id: NotBlankStr,
    ) -> WebhookDefinition | None:
        """Return one definition by ID."""
        try:
            key = UUID(definition_id)
        except ValueError:
            return None
        async with self._lock:
            return self._by_id.get(key)

    async def get_by_name(
        self,
        name: NotBlankStr,
    ) -> WebhookDefinition | None:
        """Return one definition by unique name."""
        async with self._lock:
            for definition in self._by_id.values():
                if definition.name == name:
                    return definition
        return None

    async def add(self, definition: WebhookDefinition) -> None:
        """Persist a new definition; rejects duplicate IDs or names.

        Raises:
            KeyError: If a definition with the same ID already exists
                (an ``add`` of an existing ID would silently overwrite
                the prior record, which is not what the caller asked for).
            ValueError: If another definition with the same name already
                exists under a different ID.
        """
        async with self._lock:
            if definition.id in self._by_id:
                msg = f"WebhookDefinition id already exists: {definition.id}"
                raise KeyError(msg)
            for existing in self._by_id.values():
                if existing.name == definition.name:
                    msg = f"WebhookDefinition name already exists: {definition.name!r}"
                    raise ValueError(msg)
            self._by_id[definition.id] = definition

    async def replace(self, definition: WebhookDefinition) -> None:
        """Replace an existing definition (by ID).

        Enforces the same name-uniqueness invariant as :meth:`add`:
        another definition with the same name under a different ID is
        rejected so :meth:`get_by_name` stays deterministic.

        Raises:
            KeyError: If no definition with the given ID exists.
            ValueError: If another definition with the same name exists
                under a different ID.
        """
        async with self._lock:
            if definition.id not in self._by_id:
                msg = f"WebhookDefinition id not found: {definition.id}"
                raise KeyError(msg)
            for existing in self._by_id.values():
                if existing.name == definition.name and existing.id != definition.id:
                    msg = f"WebhookDefinition name already exists: {definition.name!r}"
                    raise ValueError(msg)
            self._by_id[definition.id] = definition

    async def delete(self, definition_id: NotBlankStr) -> bool:
        """Remove a definition; returns ``True`` when one was removed.

        Returns:
            ``True`` when a definition was found and removed; ``False``
            when the ID is not a valid UUID or no matching definition
            exists.
        """
        try:
            key = UUID(definition_id)
        except ValueError:
            return False
        async with self._lock:
            return self._by_id.pop(key, None) is not None

    async def clear(self) -> None:
        """Drop all definitions (test helper)."""
        async with self._lock:
            self._by_id.clear()


__all__ = [
    "InMemoryWebhookDefinitionStore",
]
