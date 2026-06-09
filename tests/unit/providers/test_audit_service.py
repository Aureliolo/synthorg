"""Tests for ``ProviderAuditService``."""

from datetime import UTC, datetime

import pytest

from synthorg.api.dto_provider_capabilities import (
    ProviderAuditActor,
    ProviderAuditEvent,
)
from synthorg.persistence.provider_audit_protocol import ProviderAuditFilterSpec
from synthorg.providers.management.audit_service import ProviderAuditService


def _row_id(event: ProviderAuditEvent) -> int:
    """Asserting accessor for ``event.id`` after a ``record()`` call.

    The ``ProviderAuditRepo`` contract guarantees ``id`` is non-null
    on the row returned from ``record()``; the field stays optional
    on the DTO only because pre-persistence in-memory events do not
    carry one.  Tests that operate on persisted rows should assert
    that invariant instead of falling back to ``or 0``, which would
    silently mask a contract bug.
    """
    assert event.id is not None
    return event.id


class _FakeRepo:
    """In-memory ``ProviderAuditRepo`` for unit tests.

    Mirrors the protocol contract: every ``record()`` returns a row
    with a non-null monotonic ``id``; ``list()`` reads sort/filter on
    that id without defensive fallbacks.
    """

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
            key=_row_id,
            reverse=True,
        )
        if after_id is not None:
            rows = [e for e in rows if _row_id(e) < after_id]
        page = rows[:limit]
        has_more = len(rows) > limit
        return tuple(page), has_more

    async def purge_before_id(self, *, before_id: int) -> int:
        self.purged.append(before_id)
        before = len(self.records)
        self.records = [e for e in self.records if _row_id(e) >= before_id]
        return before - len(self.records)

    async def append(self, event: ProviderAuditEvent) -> None:
        await self.record(event)

    async def query(
        self,
        filter_spec: ProviderAuditFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[ProviderAuditEvent, ...]:
        rows = sorted(
            (e for e in self.records if e.provider_name == filter_spec.provider_name),
            key=_row_id,
            reverse=True,
        )
        if filter_spec.after_id is not None:
            rows = [e for e in rows if _row_id(e) < filter_spec.after_id]
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        before = len(self.records)
        self.records = [e for e in self.records if e.occurred_at >= threshold]
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
        saved_ids: list[int] = []
        for _ in range(3):
            saved = await service.record(
                provider_name="cloud-test",
                event_type="provider_updated",
                actor=actor,
            )
            saved_ids.append(_row_id(saved))
        # Purge ids strictly less than the second-recorded id.
        cutoff = saved_ids[1]
        removed = await service.purge_before_id(before_id=cutoff)
        assert removed == 1
        assert repo.purged == [cutoff]
        # Verify the *correct* row was dropped (id < cutoff) and the
        # rows at and above the cutoff survived.
        remaining_ids = sorted(_row_id(e) for e in repo.records)
        assert saved_ids[0] not in remaining_ids
        assert remaining_ids == saved_ids[1:]

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
