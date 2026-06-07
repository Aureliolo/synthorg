"""Unit tests for :class:`AgentVersionService`."""

import hashlib
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.hr.identity.version_service import AgentVersionService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability.events.agent_identity_version import (
    AGENT_IDENTITY_INVALID_REQUEST,
)
from synthorg.versioning.models import VersionSnapshot

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _make_identity(name: str = "alice") -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=NotBlankStr(name),
        role=NotBlankStr("engineer"),
        level=SeniorityLevel.MID,
        department=NotBlankStr("engineering"),
        model=ModelConfig(
            provider=NotBlankStr("test-provider"),
            model_id=NotBlankStr("test-small-001"),
        ),
        hiring_date=date(2026, 1, 1),
    )


def _snapshot(
    entity_id: str,
    version: int,
    identity: AgentIdentity | None = None,
) -> VersionSnapshot[AgentIdentity]:
    identity = identity or _make_identity()
    content = identity.model_dump_json()
    digest = hashlib.sha256(content.encode()).hexdigest()
    return VersionSnapshot[AgentIdentity](
        entity_id=NotBlankStr(entity_id),
        version=version,
        content_hash=NotBlankStr(digest),
        snapshot=identity,
        saved_by=NotBlankStr("test"),
        saved_at=_NOW,
    )


class _FakeVersionRepo:
    """Minimal in-memory ``VersionRepository[AgentIdentity]`` fake."""

    def __init__(
        self,
        snapshots: list[VersionSnapshot[AgentIdentity]],
    ) -> None:
        self._snapshots = list(snapshots)

    async def save_version(
        self,
        version: VersionSnapshot[AgentIdentity],
    ) -> bool:
        self._snapshots.append(version)
        return True

    async def get_version(
        self,
        entity_id: str,
        version: int,
    ) -> VersionSnapshot[AgentIdentity] | None:
        for snapshot in self._snapshots:
            if snapshot.entity_id == entity_id and snapshot.version == version:
                return snapshot
        return None

    async def get_latest_version(
        self,
        entity_id: str,
    ) -> VersionSnapshot[AgentIdentity] | None:
        matches = [s for s in self._snapshots if s.entity_id == entity_id]
        return max(matches, key=lambda s: s.version) if matches else None

    async def get_by_content_hash(
        self,
        entity_id: str,
        content_hash: str,
    ) -> VersionSnapshot[AgentIdentity] | None:
        for snapshot in self._snapshots:
            if (
                snapshot.entity_id == entity_id
                and snapshot.content_hash == content_hash
            ):
                return snapshot
        return None

    async def list_versions(
        self,
        entity_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[VersionSnapshot[AgentIdentity], ...]:
        matches = sorted(
            (s for s in self._snapshots if s.entity_id == entity_id),
            key=lambda s: s.version,
            reverse=True,
        )
        return tuple(matches[offset : offset + limit])

    async def count_versions(self, entity_id: str) -> int:
        return sum(1 for s in self._snapshots if s.entity_id == entity_id)

    async def delete_versions_for_entity(self, entity_id: str) -> int:
        before = len(self._snapshots)
        self._snapshots = [s for s in self._snapshots if s.entity_id != entity_id]
        return before - len(self._snapshots)


class TestListVersions:
    """Happy path + pagination + empty repo."""

    async def test_returns_newest_first_with_total(self) -> None:
        identity = _make_identity()
        snapshots = [_snapshot(str(identity.id), v, identity) for v in (1, 2, 3)]
        repo = _FakeVersionRepo(snapshots)
        service = AgentVersionService(version_repo=repo)

        page, total = await service.list_versions(
            NotBlankStr(str(identity.id)),
            offset=0,
            limit=50,
        )

        assert total == 3
        assert [s.version for s in page] == [3, 2, 1]

    async def test_paginates(self) -> None:
        identity = _make_identity()
        snapshots = [_snapshot(str(identity.id), v, identity) for v in range(1, 6)]
        service = AgentVersionService(version_repo=_FakeVersionRepo(snapshots))

        page, total = await service.list_versions(
            NotBlankStr(str(identity.id)),
            offset=2,
            limit=2,
        )

        assert total == 5
        assert [s.version for s in page] == [3, 2]

    async def test_empty_repo_returns_zero_total(self) -> None:
        service = AgentVersionService(version_repo=_FakeVersionRepo([]))

        page, total = await service.list_versions(
            NotBlankStr("agent-xyz"),
            offset=0,
            limit=50,
        )

        assert total == 0
        assert page == ()

    async def test_offset_past_end_returns_empty_page_full_total(self) -> None:
        identity = _make_identity()
        snapshots = [_snapshot(str(identity.id), v, identity) for v in (1, 2)]
        service = AgentVersionService(version_repo=_FakeVersionRepo(snapshots))

        page, total = await service.list_versions(
            NotBlankStr(str(identity.id)),
            offset=10,
            limit=5,
        )

        assert total == 2
        assert page == ()

    async def test_negative_offset_rejects(self) -> None:
        identity = _make_identity()
        service = AgentVersionService(version_repo=_FakeVersionRepo([]))

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(ValueError, match="offset"),
        ):
            await service.list_versions(
                NotBlankStr(str(identity.id)),
                offset=-1,
                limit=10,
            )
        assert any(e.get("event") == AGENT_IDENTITY_INVALID_REQUEST for e in events), (
            "invalid-request event must fire before the ValueError"
        )

    async def test_non_positive_limit_rejects(self) -> None:
        identity = _make_identity()
        service = AgentVersionService(version_repo=_FakeVersionRepo([]))

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(ValueError, match="limit"),
        ):
            await service.list_versions(
                NotBlankStr(str(identity.id)),
                offset=0,
                limit=0,
            )
        assert any(e.get("event") == AGENT_IDENTITY_INVALID_REQUEST for e in events), (
            "invalid-request event must fire before the ValueError"
        )


class TestGetVersion:
    """Existence + missing version."""

    async def test_returns_snapshot_when_found(self) -> None:
        identity = _make_identity()
        snapshot = _snapshot(str(identity.id), 2, identity)
        service = AgentVersionService(
            version_repo=_FakeVersionRepo([snapshot]),
        )

        result = await service.get_version(
            NotBlankStr(str(identity.id)),
            version=2,
        )

        assert result is snapshot

    async def test_returns_none_for_missing_version(self) -> None:
        service = AgentVersionService(version_repo=_FakeVersionRepo([]))

        result = await service.get_version(
            NotBlankStr("agent-xyz"),
            version=1,
        )

        assert result is None

    async def test_returns_none_when_version_number_mismatches(
        self,
    ) -> None:
        identity = _make_identity()
        snapshot = _snapshot(str(identity.id), 1, identity)
        service = AgentVersionService(
            version_repo=_FakeVersionRepo([snapshot]),
        )

        result = await service.get_version(
            NotBlankStr(str(identity.id)),
            version=2,
        )

        assert result is None
