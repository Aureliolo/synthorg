"""Conformance tests for ``MeetingCooldownRepository`` (both backends)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.meeting_cooldown_protocol import MeetingCooldownRecord
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(
    *,
    name: str = "daily-standup",
    when: datetime | None = None,
) -> MeetingCooldownRecord:
    return MeetingCooldownRecord(
        meeting_type_name=NotBlankStr(name),
        last_triggered_at=when or datetime.now(UTC),
    )


class TestMeetingCooldownRepository:
    async def test_save_and_load_all(self, backend: PersistenceBackend) -> None:
        await backend.meeting_cooldown.save(_record(name="daily"))
        await backend.meeting_cooldown.save(_record(name="weekly"))

        rows = await backend.meeting_cooldown.load_all()
        names = {r.meeting_type_name for r in rows}
        assert "daily" in names
        assert "weekly" in names

    async def test_save_replaces(self, backend: PersistenceBackend) -> None:
        first = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        second = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
        await backend.meeting_cooldown.save(_record(name="daily", when=first))
        await backend.meeting_cooldown.save(_record(name="daily", when=second))

        rows = await backend.meeting_cooldown.load_all()
        matched = [r for r in rows if r.meeting_type_name == "daily"]
        assert len(matched) == 1
        assert matched[0].last_triggered_at == second

    async def test_load_all_empty(self, backend: PersistenceBackend) -> None:
        rows = await backend.meeting_cooldown.load_all()
        assert rows == ()

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.meeting_cooldown.save(_record(name="daily"))

        deleted = await backend.meeting_cooldown.delete(NotBlankStr("daily"))
        assert deleted is True

        rows = await backend.meeting_cooldown.load_all()
        assert not any(r.meeting_type_name == "daily" for r in rows)

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.meeting_cooldown.delete(NotBlankStr("ghost"))
        assert deleted is False

    async def test_get_returns_record_or_none(
        self, backend: PersistenceBackend
    ) -> None:
        when = datetime(2026, 5, 14, 9, 0, tzinfo=UTC)
        await backend.meeting_cooldown.save(_record(name="daily", when=when))

        found = await backend.meeting_cooldown.get(NotBlankStr("daily"))
        assert found is not None
        assert found.last_triggered_at == when

        missing = await backend.meeting_cooldown.get(NotBlankStr("ghost"))
        assert missing is None

    async def test_list_items_orders_by_name(self, backend: PersistenceBackend) -> None:
        await backend.meeting_cooldown.save(_record(name="weekly"))
        await backend.meeting_cooldown.save(_record(name="daily"))

        page = await backend.meeting_cooldown.list_items(limit=10)
        names = [r.meeting_type_name for r in page]
        assert names == sorted(names)

    async def test_list_items_paginates(self, backend: PersistenceBackend) -> None:
        for n in ("a", "b", "c"):
            await backend.meeting_cooldown.save(_record(name=n))
        page = await backend.meeting_cooldown.list_items(limit=1, offset=1)
        assert len(page) == 1
        assert page[0].meeting_type_name == "b"

    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.meeting_cooldown.list_items(limit=0)
        with pytest.raises(QueryError):
            await backend.meeting_cooldown.list_items(offset=-1)
