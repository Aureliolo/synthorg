"""Tests for CopyOnWriteIdentityStore."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.identity.store.copy_on_write import CopyOnWriteIdentityStore
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.versioning.models import VersionSnapshot
from synthorg.versioning.service import VersioningService


def _make_identity(
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="cow-agent",
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
    return VersionSnapshot(
        entity_id=str(identity.id),
        version=version,
        content_hash="b" * 64,
        snapshot=identity,
        saved_by="test",
        saved_at=datetime.now(UTC),
    )


class TestCopyOnWritePut:
    """put() stores identity and updates version pointer."""

    @pytest.mark.unit
    async def test_put_updates_pointer(self) -> None:
        identity = _make_identity()

        registry = AgentRegistryService()
        await registry.register(identity)

        repo = AsyncMock()
        repo.get_latest_version = AsyncMock(return_value=None)
        repo.save_version = AsyncMock(return_value=True)
        versioning = VersioningService(repo)

        store = CopyOnWriteIdentityStore(registry=registry, versioning=versioning)

        evolved = identity.model_copy(
            update={"level": SeniorityLevel.SENIOR},
        )
        result = await store.put(
            str(identity.id),
            evolved,
            saved_by="test",
        )
        assert result.version == 1
        assert store._current_version[str(identity.id)] == 1


class TestCopyOnWriteGetCurrent:
    """get_current() resolves via pointer or registry fallback."""

    @pytest.mark.unit
    async def test_uses_pointer_when_set(self) -> None:
        identity_v1 = _make_identity(level=SeniorityLevel.JUNIOR)
        identity_v2 = identity_v1.model_copy(
            update={"level": SeniorityLevel.SENIOR},
        )
        snap_v1 = _make_snapshot(identity_v1, version=1)

        registry = AgentRegistryService()
        await registry.register(identity_v2)

        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=snap_v1)
        versioning = VersioningService(repo)

        store = CopyOnWriteIdentityStore(registry=registry, versioning=versioning)
        store._current_version[str(identity_v1.id)] = 1

        result = await store.get_current(str(identity_v1.id))
        assert result is not None
        assert result.level == SeniorityLevel.JUNIOR

    @pytest.mark.unit
    async def test_falls_back_to_registry(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        repo = AsyncMock()
        versioning = VersioningService(repo)
        store = CopyOnWriteIdentityStore(registry=registry, versioning=versioning)

        result = await store.get_current(str(identity.id))
        assert result is not None
        assert str(result.name) == "cow-agent"


class TestCopyOnWriteSetCurrent:
    """set_current() updates pointer without appending version."""

    @pytest.mark.unit
    async def test_rollback_updates_pointer(self) -> None:
        identity_v1 = _make_identity(level=SeniorityLevel.JUNIOR)
        identity_v2 = identity_v1.model_copy(
            update={"level": SeniorityLevel.SENIOR},
        )
        snap_v1 = _make_snapshot(identity_v1, version=1)

        registry = AgentRegistryService()
        await registry.register(identity_v2)

        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=snap_v1)
        versioning = VersioningService(repo)

        store = CopyOnWriteIdentityStore(registry=registry, versioning=versioning)
        store._current_version[str(identity_v1.id)] = 2

        restored = await store.set_current(str(identity_v1.id), 1)
        assert restored.level == SeniorityLevel.JUNIOR
        assert store._current_version[str(identity_v1.id)] == 1

        # Repo.save_version should NOT have been called (no append).
        repo.save_version.assert_not_called()

    @pytest.mark.unit
    async def test_rollback_missing_version_raises(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        repo = AsyncMock()
        repo.get_version = AsyncMock(return_value=None)
        versioning = VersioningService(repo)

        store = CopyOnWriteIdentityStore(registry=registry, versioning=versioning)

        with pytest.raises(ValueError, match="Version 99 not found"):
            await store.set_current(str(identity.id), 99)
