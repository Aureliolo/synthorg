"""Error-translation coverage for the WP-1 singleton-per-key sqlite repos.

``meeting_cooldown`` / ``tracked_container`` /
``ceremony_scheduler_state`` each wrap every method's DB call in the
same ``except (sqlite3.Error, aiosqlite.Error) -> QueryError`` block.
Patching ``_db.execute`` to raise drives that block in every method so
the copy-pasted translation path is covered once per repo.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
)
from synthorg.persistence.meeting_cooldown_protocol import MeetingCooldownRecord
from synthorg.persistence.sqlite.ceremony_scheduler_state_repo import (
    SQLiteCeremonySchedulerStateRepository,
)
from synthorg.persistence.sqlite.meeting_cooldown_repo import (
    SQLiteMeetingCooldownRepository,
)
from synthorg.persistence.sqlite.tracked_container_repo import (
    SQLiteTrackedContainerRepository,
)
from synthorg.persistence.tracked_container_protocol import TrackedContainerRecord
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(migrated_db: aiosqlite.Connection) -> aiosqlite.Connection:
    return migrated_db


def _cooldown() -> MeetingCooldownRecord:
    return MeetingCooldownRecord(
        meeting_type_name=NotBlankStr("standup"),
        last_triggered_at=_NOW,
    )


def _container() -> TrackedContainerRecord:
    return TrackedContainerRecord(
        container_id=NotBlankStr("ctr-1"),
        sidecar_id=None,
        created_at=_NOW,
    )


def _ceremony_state() -> CeremonySchedulerStateRecord:
    return CeremonySchedulerStateRecord(
        sprint_id=NotBlankStr("sprint-1"),
        completion_counters_json="{}",
        fired_once_triggers_json="[]",
        total_completions=0,
        velocity_history_json="[]",
        updated_at=_NOW,
    )


@pytest.mark.unit
class TestMeetingCooldownErrorPaths:
    @pytest.fixture
    def repo(self, db: aiosqlite.Connection) -> SQLiteMeetingCooldownRepository:
        return SQLiteMeetingCooldownRepository(
            db, write_context=make_private_write_context()
        )

    async def test_save_translates_db_error(
        self, repo: SQLiteMeetingCooldownRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.save(_cooldown())

    async def test_get_translates_db_error(
        self, repo: SQLiteMeetingCooldownRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.get(NotBlankStr("standup"))

    async def test_load_all_translates_db_error(
        self, repo: SQLiteMeetingCooldownRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.load_all()

    async def test_list_items_translates_db_error(
        self, repo: SQLiteMeetingCooldownRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.list_items(limit=10, offset=0)

    async def test_delete_translates_db_error(
        self, repo: SQLiteMeetingCooldownRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.delete(NotBlankStr("standup"))

    async def test_save_then_get_round_trip(
        self, repo: SQLiteMeetingCooldownRepository
    ) -> None:
        await repo.save(_cooldown())
        fetched = await repo.get(NotBlankStr("standup"))
        assert fetched is not None
        assert fetched.meeting_type_name == "standup"
        assert await repo.delete(NotBlankStr("standup")) is True
        assert await repo.get(NotBlankStr("standup")) is None


@pytest.mark.unit
class TestTrackedContainerErrorPaths:
    @pytest.fixture
    def repo(self, db: aiosqlite.Connection) -> SQLiteTrackedContainerRepository:
        return SQLiteTrackedContainerRepository(
            db, write_context=make_private_write_context()
        )

    async def test_save_translates_db_error(
        self, repo: SQLiteTrackedContainerRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.save(_container())

    async def test_get_translates_db_error(
        self, repo: SQLiteTrackedContainerRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.get(NotBlankStr("ctr-1"))

    async def test_delete_translates_db_error(
        self, repo: SQLiteTrackedContainerRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.delete(NotBlankStr("ctr-1"))

    async def test_load_all_translates_db_error(
        self, repo: SQLiteTrackedContainerRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.load_all()

    async def test_list_items_translates_db_error(
        self, repo: SQLiteTrackedContainerRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.list_items(limit=10, offset=0)

    async def test_save_then_get_round_trip(
        self, repo: SQLiteTrackedContainerRepository
    ) -> None:
        await repo.save(_container())
        fetched = await repo.get(NotBlankStr("ctr-1"))
        assert fetched is not None
        assert fetched.container_id == "ctr-1"
        assert await repo.delete(NotBlankStr("ctr-1")) is True


@pytest.mark.unit
class TestCeremonySchedulerStateErrorPaths:
    @pytest.fixture
    def repo(self, db: aiosqlite.Connection) -> SQLiteCeremonySchedulerStateRepository:
        return SQLiteCeremonySchedulerStateRepository(
            db, write_context=make_private_write_context()
        )

    async def test_save_translates_db_error(
        self, repo: SQLiteCeremonySchedulerStateRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.save(_ceremony_state())

    async def test_get_translates_db_error(
        self, repo: SQLiteCeremonySchedulerStateRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.get(NotBlankStr("sprint-1"))

    async def test_delete_translates_db_error(
        self, repo: SQLiteCeremonySchedulerStateRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.delete(NotBlankStr("sprint-1"))

    async def test_list_items_translates_db_error(
        self, repo: SQLiteCeremonySchedulerStateRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.list_items(limit=10, offset=0)

    async def test_save_then_get_round_trip(
        self, repo: SQLiteCeremonySchedulerStateRepository
    ) -> None:
        await repo.save(_ceremony_state())
        fetched = await repo.get(NotBlankStr("sprint-1"))
        assert fetched is not None
        assert fetched.sprint_id == "sprint-1"
        assert await repo.delete(NotBlankStr("sprint-1")) is True
