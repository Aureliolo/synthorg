"""Tests for ``ProviderAuditService``."""

from datetime import UTC, datetime

import pytest

from synthorg.api.dto_provider_capabilities import (
    ProviderAuditActor,
    ProviderAuditEvent,
)
from synthorg.providers.management.audit_service import ProviderAuditService


class _FakeRepo:
    """In-memory ``ProviderAuditRepo`` for unit tests."""

    def __init__(self) -> None:
        self.records: list[ProviderAuditEvent] = []
        self._next_id = 1
        self.purged: list[int] = []

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        saved = event.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self.records.append(saved)
        return saved

    async def list(
        self,
        *,
        provider_name: str,
        after_id: int | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        rows = sorted(
            (e for e in self.records if e.provider_name == provider_name),
            key=lambda e: e.id or 0,
            reverse=True,
        )
        if after_id is not None:
            rows = [e for e in rows if (e.id or 0) < after_id]
        page = rows[:limit]
        has_more = len(rows) > limit
        return tuple(page), has_more

    async def purge_before_id(self, *, before_id: int) -> int:
        self.purged.append(before_id)
        before = len(self.records)
        self.records = [e for e in self.records if (e.id or 0) >= before_id]
        return before - len(self.records)


@pytest.fixture
def actor() -> ProviderAuditActor:
    return ProviderAuditActor(id="user-1", label="Operator")


@pytest.mark.unit
class TestProviderAuditService:
    async def test_record_assigns_id(self, actor: ProviderAuditActor) -> None:
        repo = _FakeRepo()
        service = ProviderAuditService(repo)
        saved = await service.record(
            provider_name="cloud-test",
            event_type="provider_created",
            actor=actor,
            payload={"driver": "litellm"},
        )
        assert saved.id == 1
        assert saved.provider_name == "cloud-test"
        assert saved.payload == {"driver": "litellm"}

    async def test_list_returns_newest_first(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        repo = _FakeRepo()
        service = ProviderAuditService(repo)
        for i in range(3):
            await service.record(
                provider_name="cloud-test",
                event_type="provider_updated",
                actor=actor,
                payload={"i": i},
            )
        events, has_more = await service.list_for_provider(
            provider_name="cloud-test",
            limit=10,
        )
        assert has_more is False
        assert [e.payload["i"] for e in events] == [2, 1, 0]

    async def test_list_keyset_pagination(self, actor: ProviderAuditActor) -> None:
        repo = _FakeRepo()
        service = ProviderAuditService(repo)
        for i in range(5):
            await service.record(
                provider_name="cloud-test",
                event_type="provider_updated",
                actor=actor,
                payload={"i": i},
            )
        first, has_more = await service.list_for_provider(
            provider_name="cloud-test",
            limit=2,
        )
        assert len(first) == 2
        assert has_more is True

        cursor = first[-1].id
        assert cursor is not None
        second, _ = await service.list_for_provider(
            provider_name="cloud-test",
            after_id=cursor,
            limit=2,
        )
        assert all(e.id is not None and e.id < cursor for e in second)

    async def test_list_isolates_providers(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        repo = _FakeRepo()
        service = ProviderAuditService(repo)
        await service.record(
            provider_name="cloud-a",
            event_type="provider_created",
            actor=actor,
        )
        await service.record(
            provider_name="cloud-b",
            event_type="provider_created",
            actor=actor,
        )
        events, _ = await service.list_for_provider(provider_name="cloud-a")
        assert all(e.provider_name == "cloud-a" for e in events)
        assert len(events) == 1

    async def test_purge_before_id(self, actor: ProviderAuditActor) -> None:
        repo = _FakeRepo()
        service = ProviderAuditService(repo)
        for _ in range(3):
            await service.record(
                provider_name="cloud-test",
                event_type="provider_updated",
                actor=actor,
            )
        removed = await service.purge_before_id(before_id=2)
        assert removed == 1
        assert repo.purged == [2]

    async def test_record_uses_now_utc(self, actor: ProviderAuditActor) -> None:
        repo = _FakeRepo()
        service = ProviderAuditService(repo)
        before = datetime.now(UTC)
        saved = await service.record(
            provider_name="cloud-test",
            event_type="provider_updated",
            actor=actor,
        )
        after = datetime.now(UTC)
        assert before <= saved.occurred_at <= after
