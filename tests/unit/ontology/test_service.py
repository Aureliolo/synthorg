"""Tests for OntologyService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.ontology.config import EntitiesConfig, EntityEntry, OntologyConfig
from synthorg.ontology.decorator import clear_entity_registry, ontology_entity
from synthorg.ontology.errors import OntologyDuplicateError
from synthorg.ontology.models import (
    EntityDefinition,
    EntitySource,
    EntityTier,
)
from synthorg.ontology.service import OntologyService

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_entity(
    name: str = "Task",
    *,
    tier: EntityTier = EntityTier.CORE,
    source: EntitySource = EntitySource.AUTO,
) -> EntityDefinition:
    return EntityDefinition(
        name=name,
        tier=tier,
        source=source,
        definition="A sample entity.",
        created_by="system",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def mock_backend() -> AsyncMock:
    """A mock OntologyEntityRepository."""
    backend = AsyncMock()
    backend.register = AsyncMock()
    backend.get = AsyncMock()
    backend.update = AsyncMock()
    backend.delete = AsyncMock()
    backend.list_entities = AsyncMock(return_value=())
    backend.search = AsyncMock(return_value=())
    backend.get_version_manifest = AsyncMock(return_value={})
    return backend


@pytest.fixture
def mock_versioning() -> AsyncMock:
    """A mock VersioningService."""
    vs = AsyncMock()
    vs.snapshot_if_changed = AsyncMock(return_value=None)
    return vs


@pytest.fixture
def service(
    mock_backend: AsyncMock,
    mock_versioning: AsyncMock,
) -> OntologyService:
    """An OntologyService with mocked dependencies."""
    return OntologyService(
        backend=mock_backend,
        versioning=mock_versioning,
        config=OntologyConfig(),
    )


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Clear the decorator registry before each test."""
    clear_entity_registry()


# ── Bootstrap ───────────────────────────────────────────────────


class TestBootstrap:
    async def test_bootstrap_registers_decorated_entities(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
        mock_versioning: AsyncMock,
    ) -> None:
        from pydantic import BaseModel, Field

        @ontology_entity
        class SampleEntity(BaseModel):
            """A sample entity for bootstrap testing."""

            title: str = Field(description="Title")

        await service.bootstrap()

        mock_backend.register.assert_called_once()
        registered = mock_backend.register.call_args[0][0]
        assert registered.name == "SampleEntity"
        assert registered.tier == EntityTier.CORE
        assert registered.source == EntitySource.AUTO
        mock_versioning.snapshot_if_changed.assert_called_once()

    async def test_bootstrap_skips_already_registered(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
    ) -> None:
        from pydantic import BaseModel

        @ontology_entity
        class Existing(BaseModel):
            """Existing."""

        mock_backend.register.side_effect = OntologyDuplicateError("exists")
        await service.bootstrap()

        # Should not raise, just skip.
        mock_backend.register.assert_called_once()

    async def test_bootstrap_from_config(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
        mock_versioning: AsyncMock,
    ) -> None:
        entries = EntitiesConfig(
            entries=(
                EntityEntry(
                    name="CustomEntity",
                    definition="A custom entity.",
                    fields={"field1": "First field"},
                ),
            ),
        )
        await service.bootstrap_from_config(entries)

        mock_backend.register.assert_called_once()
        registered = mock_backend.register.call_args[0][0]
        assert registered.name == "CustomEntity"
        assert registered.tier == EntityTier.USER
        assert registered.source == EntitySource.CONFIG
        assert len(registered.fields) == 1
        assert registered.fields[0].name == "field1"
        mock_versioning.snapshot_if_changed.assert_called_once()


# ── CRUD delegation ─────────────────────────────────────────────


class TestCrudDelegation:
    async def test_register_delegates_and_snapshots(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
        mock_versioning: AsyncMock,
    ) -> None:
        entity = _make_entity()
        await service.register(entity)
        mock_backend.register.assert_called_once_with(entity)
        mock_versioning.snapshot_if_changed.assert_called_once()

    async def test_update_delegates_and_snapshots(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
        mock_versioning: AsyncMock,
    ) -> None:
        entity = _make_entity()
        await service.update(entity)
        mock_backend.update.assert_called_once_with(entity)
        mock_versioning.snapshot_if_changed.assert_called_once()

    async def test_delete_delegates(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
    ) -> None:
        await service.delete("Task")
        mock_backend.delete.assert_called_once_with("Task")

    async def test_get_delegates(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
    ) -> None:
        expected = _make_entity()
        mock_backend.get.return_value = expected
        result = await service.get("Task")
        assert result == expected
        mock_backend.get.assert_called_once_with("Task")

    async def test_list_entities_delegates(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
    ) -> None:
        mock_backend.list_entities.return_value = (_make_entity(),)
        result = await service.list_entities(tier=EntityTier.CORE)
        assert len(result) == 1
        mock_backend.list_entities.assert_called_once_with(
            tier=EntityTier.CORE,
        )

    async def test_search_delegates(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
    ) -> None:
        mock_backend.search.return_value = (_make_entity(),)
        result = await service.search("Task")
        assert len(result) == 1
        mock_backend.search.assert_called_once_with("Task")


# ── Version manifest ────────────────────────────────────────────


class TestVersionManifest:
    async def test_get_version_manifest_delegates(
        self,
        service: OntologyService,
        mock_backend: AsyncMock,
    ) -> None:
        mock_backend.get_version_manifest.return_value = {"Task": 3}
        result = await service.get_version_manifest()
        assert result == {"Task": 3}
