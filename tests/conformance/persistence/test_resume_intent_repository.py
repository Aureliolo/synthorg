"""Conformance tests for ``ResumeIntentRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.resume_intent import ResumeIntent
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _intent(approval_id: str = "appr-001") -> ResumeIntent:
    return ResumeIntent(
        approval_id=NotBlankStr(approval_id),
        recorded_at=_RECORDED_AT,
    )


class TestResumeIntentRepository:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.resume_intents.save(_intent())

        loaded = await backend.resume_intents.get(NotBlankStr("appr-001"))
        assert loaded is not None
        assert loaded.approval_id == "appr-001"
        assert loaded.recorded_at == _RECORDED_AT

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.resume_intents.get(NotBlankStr("nope")) is None

    async def test_save_keeps_the_earliest_marker(
        self, backend: PersistenceBackend
    ) -> None:
        # Insert-if-absent, not an upsert: a losing concurrent decider must
        # not overwrite the winner's earlier timestamp, which is what the
        # startup drain uses to tell a bracketing marker from a stale one.
        later = datetime(2026, 7, 24, 13, 0, tzinfo=UTC)
        await backend.resume_intents.save(_intent())
        await backend.resume_intents.save(
            ResumeIntent(approval_id=NotBlankStr("appr-001"), recorded_at=later),
        )

        rows = await backend.resume_intents.list_items()
        assert len(rows) == 1
        assert rows[0].recorded_at == _RECORDED_AT

    async def test_list_items_is_approval_id_ordered(
        self, backend: PersistenceBackend
    ) -> None:
        for approval_id in ("appr-003", "appr-001", "appr-002"):
            await backend.resume_intents.save(_intent(approval_id))

        rows = await backend.resume_intents.list_items()
        assert [row.approval_id for row in rows] == [
            "appr-001",
            "appr-002",
            "appr-003",
        ]

    async def test_list_items_paginates(self, backend: PersistenceBackend) -> None:
        for approval_id in ("appr-001", "appr-002", "appr-003"):
            await backend.resume_intents.save(_intent(approval_id))

        page = await backend.resume_intents.list_items(limit=2, offset=1)
        assert [row.approval_id for row in page] == ["appr-002", "appr-003"]

    async def test_delete_clears_the_marker(self, backend: PersistenceBackend) -> None:
        await backend.resume_intents.save(_intent())

        assert await backend.resume_intents.delete(NotBlankStr("appr-001")) is True
        assert await backend.resume_intents.get(NotBlankStr("appr-001")) is None

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        # The clear path runs unconditionally after a settled resume, so a
        # already-absent marker must be a quiet no-op rather than an error.
        assert await backend.resume_intents.delete(NotBlankStr("nope")) is False
