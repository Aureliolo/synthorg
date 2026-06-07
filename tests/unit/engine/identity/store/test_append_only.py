"""Tests for AppendOnlyIdentityStore."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.identity.store.append_only import AppendOnlyIdentityStore
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.versioning.models import VersionSnapshot
from synthorg.versioning.service import VersioningService


def _make_identity(
    *,
    agent_id: str | None = None,
    name: str = "test-agent",
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    """Build a minimal valid AgentIdentity for testing."""
    from uuid import UUID

    return AgentIdentity(
        id=UUID(agent_id) if agent_id else uuid4(),
        name=name,
        role="test-role",
        department="engineering",
        level=level,
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _make_snapshot(
    identity: AgentIdentity,
    version: int = 1,
) -> VersionSnapshot[AgentIdentity]:
    """Build a VersionSnapshot for testing."""
    return VersionSnapshot(
        entity_id=str(identity.id),
        version=version,
        content_hash="a" * 64,
        snapshot=identity,
        saved_by="test",
        saved_at=datetime.now(UTC),
    )


class TestAppendOnlyPut:
    """put() stores identity and creates version snapshot."""

    @pytest.mark.unit
    async def test_put_registers_and_snapshots(self) -> None:
        identity = _make_identity()

        registry = AgentRegistryService()
        await registry.register(identity)

        repo = AsyncMock()
        repo.get_latest_version = AsyncMock(return_value=None)
        repo.save_version = AsyncMock(return_value=True)

        versioning = VersioningService(repo)

        store = AppendOnlyIdentityStore(
            registry=registry,
            versioning=versioning,
        )

        evolved = identity.model_copy(
            update={"level": SeniorityLevel.SENIOR},
        )
        result = await store.put(
            str(identity.id),
            evolved,
            saved_by="test-evolution",
        )
        assert result.version == 1

        # Registry should have the evolved identity.
        current = await registry.get(str(identity.id))
        assert current is not None
        assert current.level == SeniorityLevel.SENIOR


class TestAppendOnlyGetCurrent:
    """get_current() delegates to registry."""

    @pytest.mark.unit
    async def test_returns_current_from_registry(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        repo = AsyncMock()
        versioning = VersioningService(repo)
        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        result = await store.get_current(str(identity.id))
        assert result is not None
        assert str(result.name) == "test-agent"

    @pytest.mark.unit
    async def test_returns_none_for_unknown(self) -> None:
        registry = AgentRegistryService()
        repo = AsyncMock()
        versioning = VersioningService(repo)
        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        result = await store.get_current("nonexistent")
        assert result is None


class TestAppendOnlyGetVersion:
    """get_version() retrieves specific version snapshots."""

    @pytest.mark.unit
    async def test_returns_snapshot_content(self) -> None:
        identity = _make_identity()
        snapshot = _make_snapshot(identity, version=3)

        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=snapshot)
        versioning = VersioningService(repo)

        registry = AgentRegistryService()
        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        result = await store.get_version(str(identity.id), 3)
        assert result is not None
        assert str(result.name) == "test-agent"

    @pytest.mark.unit
    async def test_returns_none_for_missing_version(self) -> None:
        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=None)
        versioning = VersioningService(repo)

        registry = AgentRegistryService()
        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        result = await store.get_version("agent-1", 999)
        assert result is None


class TestAppendOnlySetCurrent:
    """set_current() rolls back and appends a new version."""

    @pytest.mark.unit
    async def test_rollback_restores_identity(self) -> None:
        identity_v1 = _make_identity(level=SeniorityLevel.JUNIOR)
        identity_v2 = identity_v1.model_copy(
            update={"level": SeniorityLevel.SENIOR},
        )

        snap_v1 = _make_snapshot(identity_v1, version=1)
        snap_v2 = _make_snapshot(identity_v2, version=2)

        registry = AgentRegistryService()
        await registry.register(identity_v2)

        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=snap_v1)
        repo.get_latest_version = AsyncMock(return_value=snap_v2)
        repo.save_version = AsyncMock(return_value=True)
        versioning = VersioningService(repo)

        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        restored = await store.set_current(str(identity_v1.id), 1)
        assert restored.level == SeniorityLevel.JUNIOR

        # Registry should reflect the rollback.
        current = await registry.get(str(identity_v1.id))
        assert current is not None
        assert current.level == SeniorityLevel.JUNIOR

    @pytest.mark.unit
    async def test_rollback_nonexistent_version_raises(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=None)
        versioning = VersioningService(repo)

        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        with pytest.raises(ValueError, match="Version 99 not found"):
            await store.set_current(str(identity.id), 99)


class TestAppendOnlyListVersions:
    """list_versions() delegates to repo."""

    @pytest.mark.unit
    async def test_delegates_to_repo(self) -> None:
        identity = _make_identity()
        snap = _make_snapshot(identity)

        repo = AsyncMock()
        repo.list_versions = AsyncMock(return_value=(snap,))
        versioning = VersioningService(repo)

        registry = AgentRegistryService()
        store = AppendOnlyIdentityStore(registry=registry, versioning=versioning)

        result = await store.list_versions(str(identity.id))
        assert len(result) == 1
        assert result[0].version == 1
