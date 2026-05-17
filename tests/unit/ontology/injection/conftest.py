"""Shared fixtures for ontology injection tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.ontology.models import (
    EntityDefinition,
    EntityField,
    EntitySource,
    EntityTier,
)

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_entity(
    name: str,
    *,
    tier: EntityTier = EntityTier.CORE,
    definition: str = "",
    fields: tuple[EntityField, ...] = (),
) -> EntityDefinition:
    """Create an entity definition with defaults."""
    return EntityDefinition(
        name=name,
        tier=tier,
        source=EntitySource.AUTO,
        definition=definition,
        fields=fields,
        created_by="system",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def core_entities() -> tuple[EntityDefinition, ...]:
    """Two CORE-tier entities."""
    return (
        _make_entity(
            "Task",
            definition="A unit of work within the company.",
            fields=(
                EntityField(
                    name="title",
                    type_hint="str",
                    description="Task title",
                ),
                EntityField(
                    name="status",
                    type_hint="TaskStatus",
                    description="Current status",
                ),
            ),
        ),
        _make_entity(
            "AgentIdentity",
            definition="An agent's identity and capabilities.",
            fields=(
                EntityField(
                    name="name",
                    type_hint="str",
                    description="Agent name",
                ),
            ),
        ),
    )


@pytest.fixture
def user_entity() -> EntityDefinition:
    """A USER-tier entity."""
    return _make_entity(
        "Invoice",
        tier=EntityTier.USER,
        definition="A financial document.",
    )


@pytest.fixture
def mock_backend(
    core_entities: tuple[EntityDefinition, ...],
    user_entity: EntityDefinition,
) -> AsyncMock:
    """Mock OntologyEntityRepository with pre-loaded entities."""
    backend = AsyncMock()
    backend.is_connected = True
    backend.backend_name = "mock"

    all_entities = (*core_entities, user_entity)

    async def list_entities(
        *,
        tier: EntityTier | None = None,
    ) -> tuple[EntityDefinition, ...]:
        if tier is None:
            return all_entities
        return tuple(e for e in all_entities if e.tier == tier)

    async def get(name: str) -> EntityDefinition:
        for e in all_entities:
            if e.name == name:
                return e
        from synthorg.ontology.errors import OntologyNotFoundError

        raise OntologyNotFoundError(name)

    async def search(query: str) -> tuple[EntityDefinition, ...]:
        return tuple(
            e
            for e in all_entities
            if query.lower() in e.name.lower() or query.lower() in e.definition.lower()
        )

    async def get_version_manifest() -> dict[str, int]:
        return {e.name: 1 for e in all_entities}

    backend.list_entities = AsyncMock(side_effect=list_entities)
    backend.get = AsyncMock(side_effect=get)
    backend.search = AsyncMock(side_effect=search)
    backend.get_version_manifest = AsyncMock(
        side_effect=get_version_manifest,
    )
    return backend
