"""Tests for OntologyOrgMemorySync."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.memory.enums import OrgFactCategory
from synthorg.ontology.config import OntologySyncConfig
from synthorg.ontology.models import (
    EntityDefinition,
    EntitySource,
    EntityTier,
)
from synthorg.ontology.sync import OntologyOrgMemorySync

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_entity(name: str, definition: str = "test") -> EntityDefinition:
    return EntityDefinition(
        name=name,
        tier=EntityTier.CORE,
        source=EntitySource.AUTO,
        definition=definition,
        created_by="system",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_ontology(
    entities: tuple[EntityDefinition, ...] = (),
) -> AsyncMock:
    backend = AsyncMock()
    backend.list_entities = AsyncMock(return_value=entities)
    return backend


def _make_org_memory() -> AsyncMock:
    """Create a mock OrgMemoryBackend."""
    org = AsyncMock()
    org.write = AsyncMock(return_value="fact-123")
    return org


@pytest.mark.unit
class TestSyncEntity:
    """Tests for sync_entity()."""

    async def test_publishes_entity_as_org_fact(self) -> None:
        """Entity is published to OrgMemory."""
        entity = _make_entity("Task", "A unit of work")
        ontology = _make_ontology((entity,))
        org_memory = _make_org_memory()
        config = OntologySyncConfig()

        sync = OntologyOrgMemorySync(
            ontology=ontology,
            org_memory=org_memory,
            config=config,
        )
        result = await sync.sync_entity(entity)
        assert result is True
        org_memory.write.assert_awaited_once()

        call_args = org_memory.write.call_args
        request = call_args[0][0]
        assert "Task" in request.content
        assert "entity" in request.tags
        assert "tier:core" in request.tags
        assert request.category == OrgFactCategory.ENTITY_DEFINITION

    async def test_idempotent_skip(self) -> None:
        """Second sync of unchanged entity is skipped."""
        entity = _make_entity("Task", "A unit of work")
        ontology = _make_ontology((entity,))
        org_memory = _make_org_memory()
        config = OntologySyncConfig()

        sync = OntologyOrgMemorySync(
            ontology=ontology,
            org_memory=org_memory,
            config=config,
        )
        assert await sync.sync_entity(entity) is True
        assert await sync.sync_entity(entity) is False
        assert org_memory.write.await_count == 1

    async def test_changed_entity_republished(self) -> None:
        """Modified entity is published again."""
        entity1 = _make_entity("Task", "A unit of work")
        entity2 = _make_entity("Task", "A tracked unit of work")
        ontology = _make_ontology((entity1,))
        org_memory = _make_org_memory()
        config = OntologySyncConfig()

        sync = OntologyOrgMemorySync(
            ontology=ontology,
            org_memory=org_memory,
            config=config,
        )
        assert await sync.sync_entity(entity1) is True
        assert await sync.sync_entity(entity2) is True
        assert org_memory.write.await_count == 2


@pytest.mark.unit
class TestSyncAll:
    """Tests for sync_all()."""

    async def test_syncs_all_entities(self) -> None:
        """All entities are published."""
        entities = (
            _make_entity("Task", "task def"),
            _make_entity("Agent", "agent def"),
        )
        ontology = _make_ontology(entities)
        org_memory = _make_org_memory()
        config = OntologySyncConfig()

        sync = OntologyOrgMemorySync(
            ontology=ontology,
            org_memory=org_memory,
            config=config,
        )
        count = await sync.sync_all()
        assert count == 2
        assert org_memory.write.await_count == 2

    async def test_sync_all_empty(self) -> None:
        """No entities to sync returns 0."""
        ontology = _make_ontology()
        org_memory = _make_org_memory()
        config = OntologySyncConfig()

        sync = OntologyOrgMemorySync(
            ontology=ontology,
            org_memory=org_memory,
            config=config,
        )
        count = await sync.sync_all()
        assert count == 0

    async def test_sync_all_idempotent(self) -> None:
        """Second full sync skips unchanged entities."""
        entities = (_make_entity("Task"),)
        ontology = _make_ontology(entities)
        org_memory = _make_org_memory()
        config = OntologySyncConfig()

        sync = OntologyOrgMemorySync(
            ontology=ontology,
            org_memory=org_memory,
            config=config,
        )
        assert await sync.sync_all() == 1
        assert await sync.sync_all() == 0
