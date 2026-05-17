"""Tests for OntologyAwareMemoryBackend."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.ontology.config import OntologyMemoryConfig
from synthorg.ontology.memory_wrapper import OntologyAwareMemoryBackend
from synthorg.ontology.models import (
    EntityDefinition,
    EntitySource,
    EntityTier,
)

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_entity(name: str, definition: str = "") -> EntityDefinition:
    return EntityDefinition(
        name=name,
        tier=EntityTier.CORE,
        source=EntitySource.AUTO,
        definition=definition,
        created_by="system",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_inner() -> AsyncMock:
    """Create a mock MemoryBackend."""
    inner = AsyncMock()
    inner.is_connected = True
    inner.backend_name = "mock"
    inner.store = AsyncMock(return_value="mem-123")
    inner.retrieve = AsyncMock(return_value=())
    inner.get = AsyncMock(return_value=None)
    inner.delete = AsyncMock(return_value=True)
    inner.count = AsyncMock(return_value=0)
    inner.connect = AsyncMock()
    inner.disconnect = AsyncMock()
    inner.health_check = AsyncMock(return_value=True)
    return inner


def _make_ontology(
    entities: tuple[EntityDefinition, ...] = (),
    manifest: dict[str, int] | None = None,
) -> AsyncMock:
    """Create a mock OntologyEntityRepository."""
    backend = AsyncMock()
    backend.list_entities = AsyncMock(return_value=entities)
    if manifest is None:
        manifest = {e.name: 1 for e in entities}
    backend.get_version_manifest = AsyncMock(return_value=manifest)

    async def get(name: str) -> EntityDefinition:
        for e in entities:
            if e.name == name:
                return e
        from synthorg.ontology.errors import OntologyNotFoundError

        raise OntologyNotFoundError(name)

    backend.get = AsyncMock(side_effect=get)
    return backend


def _make_request(content: str = "Update the Task status") -> MemoryStoreRequest:
    return MemoryStoreRequest(
        category=MemoryCategory.EPISODIC,
        content=content,
    )


def _make_entry(
    *,
    content: str = "Task was updated",
    tags: tuple[str, ...] = (),
) -> MemoryEntry:
    return MemoryEntry(
        id="mem-1",
        agent_id="agent-1",
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(tags=tags),
        created_at=_NOW,
    )


@pytest.mark.unit
class TestOntologyAwareStoreTagging:
    """Tests for auto-tagging on store()."""

    async def test_adds_entity_tags(self) -> None:
        """Entities found in content get entity:<name> tags."""
        entities = (_make_entity("Task", "A unit of work"),)
        inner = _make_inner()
        ontology = _make_ontology(entities)
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        request = _make_request("Update the Task status")
        await wrapper.store("agent-1", request)

        call_args = inner.store.call_args
        stored_request = call_args[0][1]
        assert "entity:Task" in stored_request.metadata.tags

    async def test_no_duplicate_tags(self) -> None:
        """Existing entity tags are not duplicated."""
        entities = (_make_entity("Task"),)
        inner = _make_inner()
        ontology = _make_ontology(entities)
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="Task update",
            metadata=MemoryMetadata(tags=("entity:Task",)),
        )
        await wrapper.store("agent-1", request)

        call_args = inner.store.call_args
        stored_request = call_args[0][1]
        tag_count = stored_request.metadata.tags.count("entity:Task")
        assert tag_count == 1

    async def test_no_tags_when_no_entities_match(self) -> None:
        """No entity tags added when content has no entity references."""
        entities = (_make_entity("Invoice"),)
        inner = _make_inner()
        ontology = _make_ontology(entities)
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        request = _make_request("Just a random note")
        await wrapper.store("agent-1", request)

        call_args = inner.store.call_args
        stored_request = call_args[0][1]
        assert not any(t.startswith("entity:") for t in stored_request.metadata.tags)

    async def test_auto_tag_disabled(self) -> None:
        """No tagging when auto_tag is False."""
        entities = (_make_entity("Task"),)
        inner = _make_inner()
        ontology = _make_ontology(entities)
        config = OntologyMemoryConfig(auto_tag=False)
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        request = _make_request("Update the Task")
        await wrapper.store("agent-1", request)

        call_args = inner.store.call_args
        stored_request = call_args[0][1]
        assert not any(t.startswith("entity:") for t in stored_request.metadata.tags)

    async def test_case_insensitive_matching(self) -> None:
        """Entity names are matched case-insensitively."""
        entities = (_make_entity("AgentIdentity"),)
        inner = _make_inner()
        ontology = _make_ontology(entities)
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        request = _make_request("The agentidentity was updated")
        await wrapper.store("agent-1", request)

        call_args = inner.store.call_args
        stored_request = call_args[0][1]
        assert "entity:AgentIdentity" in stored_request.metadata.tags

    async def test_returns_memory_id(self) -> None:
        """Store returns the backend-assigned memory ID."""
        inner = _make_inner()
        ontology = _make_ontology()
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        result = await wrapper.store("agent-1", _make_request())
        assert result == "mem-123"


@pytest.mark.unit
class TestOntologyAwareRetrieveEnrichment:
    """Tests for entity version enrichment on retrieve()."""

    async def test_enriches_tagged_entries(self) -> None:
        """Entries with entity tags get version tags."""
        entities = (_make_entity("Task"),)
        inner = _make_inner()
        inner.retrieve = AsyncMock(
            return_value=(_make_entry(tags=("entity:Task",)),),
        )
        ontology = _make_ontology(entities, manifest={"Task": 3})
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        results = await wrapper.retrieve("agent-1", MemoryQuery())
        assert len(results) == 1
        assert "entity_version:Task=3" in results[0].metadata.tags

    async def test_no_enrichment_without_tags(self) -> None:
        """Entries without entity tags are unchanged."""
        inner = _make_inner()
        inner.retrieve = AsyncMock(
            return_value=(_make_entry(),),
        )
        ontology = _make_ontology()
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        results = await wrapper.retrieve("agent-1", MemoryQuery())
        assert len(results) == 1
        assert not any(
            t.startswith("entity_version:") for t in results[0].metadata.tags
        )

    async def test_empty_retrieve(self) -> None:
        """Empty retrieve returns empty."""
        inner = _make_inner()
        ontology = _make_ontology()
        config = OntologyMemoryConfig()
        wrapper = OntologyAwareMemoryBackend(inner, ontology, config)

        results = await wrapper.retrieve("agent-1", MemoryQuery())
        assert results == ()


@pytest.mark.unit
class TestOntologyAwarePassthrough:
    """Tests for passthrough operations."""

    async def test_connect_delegates(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        await wrapper.connect()
        inner.connect.assert_awaited_once()

    async def test_disconnect_delegates(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        await wrapper.disconnect()
        inner.disconnect.assert_awaited_once()

    async def test_health_check_delegates(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        result = await wrapper.health_check()
        assert result is True

    def test_is_connected(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        assert wrapper.is_connected is True

    def test_backend_name(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        assert wrapper.backend_name == "ontology:mock"

    async def test_get_delegates(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        await wrapper.get("agent-1", "mem-1")
        inner.get.assert_awaited_once_with("agent-1", "mem-1")

    async def test_delete_delegates(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        result = await wrapper.delete("agent-1", "mem-1")
        assert result is True

    async def test_count_delegates(self) -> None:
        inner = _make_inner()
        wrapper = OntologyAwareMemoryBackend(
            inner,
            _make_ontology(),
            OntologyMemoryConfig(),
        )
        result = await wrapper.count("agent-1")
        assert result == 0
