"""Error-translation coverage for the singleton-per-key sqlite repos.

``tracked_container`` wraps every method's DB call in the same
``except (sqlite3.Error, aiosqlite.Error) -> QueryError`` block.
Patching ``_db.execute`` to raise drives that block in every method so
the translation path is covered once per repo.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
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


def _container() -> TrackedContainerRecord:
    return TrackedContainerRecord(
        container_id=NotBlankStr("ctr-1"),
        sidecar_id=None,
        created_at=_NOW,
    )


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
