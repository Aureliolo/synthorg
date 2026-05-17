"""Conformance tests for ``CeremonySchedulerStateRepository`` (both backends)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(
    *,
    sprint_id: str = "sprint-1",
    counters: str = '{"daily_standup": 3}',
    triggers: str = '["sprint_start"]',
    total: int = 5,
    velocity: str = "[]",
) -> CeremonySchedulerStateRecord:
    return CeremonySchedulerStateRecord(
        sprint_id=NotBlankStr(sprint_id),
        completion_counters_json=counters,
        fired_once_triggers_json=triggers,
        total_completions=total,
        velocity_history_json=velocity,
        updated_at=datetime.now(UTC),
    )


class TestCeremonySchedulerStateRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        rec = _record()
        await backend.ceremony_scheduler_state.save(rec)

        loaded = await backend.ceremony_scheduler_state.get(NotBlankStr("sprint-1"))
        assert loaded is not None
        assert loaded.completion_counters_json == '{"daily_standup": 3}'
        assert loaded.total_completions == 5

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        await backend.ceremony_scheduler_state.save(_record(total=1))
        await backend.ceremony_scheduler_state.save(_record(total=42))

        loaded = await backend.ceremony_scheduler_state.get(NotBlankStr("sprint-1"))
        assert loaded is not None
        assert loaded.total_completions == 42

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert (
            await backend.ceremony_scheduler_state.get(NotBlankStr("ghost-sprint"))
            is None
        )

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.ceremony_scheduler_state.save(_record())

        deleted = await backend.ceremony_scheduler_state.delete(NotBlankStr("sprint-1"))
        assert deleted is True

        assert (
            await backend.ceremony_scheduler_state.get(NotBlankStr("sprint-1")) is None
        )

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.ceremony_scheduler_state.delete(
            NotBlankStr("ghost-sprint")
        )
        assert deleted is False

    async def test_list_items_orders_by_sprint_id_and_paginates(
        self, backend: PersistenceBackend
    ) -> None:
        for sid in ("sprint-3", "sprint-1", "sprint-2"):
            await backend.ceremony_scheduler_state.save(_record(sprint_id=sid))
        rows = await backend.ceremony_scheduler_state.list_items(limit=10)
        ids = [r.sprint_id for r in rows]
        assert ids == sorted(ids)
        assert {"sprint-1", "sprint-2", "sprint-3"} <= set(ids)
        page = await backend.ceremony_scheduler_state.list_items(limit=1, offset=1)
        assert len(page) == 1
        assert page[0].sprint_id == ids[1]

    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.ceremony_scheduler_state.list_items(limit=0)
        with pytest.raises(QueryError):
            await backend.ceremony_scheduler_state.list_items(offset=-1)

    async def test_list_items_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.ceremony_scheduler_state.list_items() == ()
