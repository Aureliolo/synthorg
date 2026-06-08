"""Tests for SQLiteProjectRepository."""

from unittest.mock import patch

import aiosqlite
import pytest

from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.persistence.project_protocol import ProjectFilterSpec
from synthorg.persistence.sqlite.project_repo import SQLiteProjectRepository
from tests._shared import as_uuid, sid
from tests._shared.persistence import make_private_write_context


def _make_project(  # noqa: PLR0913
    *,
    project_id: str = "proj-001",
    name: str = "Test Project",
    description: str = "A test project",
    team: tuple[str, ...] = (),
    lead: str | None = None,
    task_ids: tuple[str, ...] = (),
    deadline: str | None = None,
    budget: float = 0.0,
    status: ProjectStatus = ProjectStatus.PLANNING,
) -> Project:
    return Project(
        id=as_uuid(project_id),
        name=name,
        description=description,
        team=team,
        lead=lead,
        task_ids=task_ids,
        deadline=deadline,
        budget=budget,
        status=status,
    )


@pytest.fixture
def repo(migrated_db: aiosqlite.Connection) -> SQLiteProjectRepository:
    return SQLiteProjectRepository(
        migrated_db, write_context=make_private_write_context()
    )


@pytest.mark.unit
class TestSQLiteProjectRepository:
    async def test_save_and_get(self, repo: SQLiteProjectRepository) -> None:
        project = _make_project()
        await repo.save(project)
        fetched = await repo.get(sid("proj-001"))
        assert fetched is not None
        assert fetched.id == as_uuid("proj-001")
        assert fetched.name == "Test Project"
        assert fetched.description == "A test project"
        assert fetched.status is ProjectStatus.PLANNING

    async def test_get_returns_none_for_missing(
        self, repo: SQLiteProjectRepository
    ) -> None:
        result = await repo.get(sid("nonexistent"))
        assert result is None

    async def test_save_upsert_updates_existing(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project()
        await repo.save(project)
        updated = project.model_copy(update={"name": "Updated Name"})
        await repo.save(updated)
        fetched = await repo.get(sid("proj-001"))
        assert fetched is not None
        assert fetched.name == "Updated Name"

    async def test_list_items_in_id_order(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(_make_project(project_id="p1", name="P1"))
        await repo.save(_make_project(project_id="p2", name="P2"))
        result = await repo.list_items()
        assert len(result) == 2
        assert result[0].id == as_uuid("p1")
        assert result[1].id == as_uuid("p2")

    async def test_query_filter_by_status(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(
            _make_project(project_id="p1", name="P1", status=ProjectStatus.ACTIVE)
        )
        await repo.save(
            _make_project(project_id="p2", name="P2", status=ProjectStatus.COMPLETED)
        )
        result = await repo.query(ProjectFilterSpec(status=ProjectStatus.ACTIVE))
        assert len(result) == 1
        assert result[0].status is ProjectStatus.ACTIVE

    async def test_query_filter_by_lead(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(_make_project(project_id="p1", name="P1", lead="alice"))
        await repo.save(_make_project(project_id="p2", name="P2", lead="bob"))
        result = await repo.query(ProjectFilterSpec(lead="alice"))
        assert len(result) == 1
        assert result[0].lead == "alice"

    async def test_query_combined_filters(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(
            _make_project(
                project_id="p1",
                name="P1",
                status=ProjectStatus.ACTIVE,
                lead="alice",
            )
        )
        await repo.save(
            _make_project(
                project_id="p2",
                name="P2",
                status=ProjectStatus.ACTIVE,
                lead="bob",
            )
        )
        await repo.save(
            _make_project(
                project_id="p3",
                name="P3",
                status=ProjectStatus.COMPLETED,
                lead="alice",
            )
        )
        result = await repo.query(
            ProjectFilterSpec(status=ProjectStatus.ACTIVE, lead="alice"),
        )
        assert len(result) == 1
        assert result[0].id == as_uuid("p1")

    async def test_delete_existing(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(_make_project())
        deleted = await repo.delete(sid("proj-001"))
        assert deleted is True
        assert await repo.get(sid("proj-001")) is None

    async def test_delete_missing(self, repo: SQLiteProjectRepository) -> None:
        deleted = await repo.delete(sid("nonexistent"))
        assert deleted is False

    async def test_roundtrip_preserves_team_and_task_ids(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project(
            team=("agent-1", "agent-2", "agent-3"),
            task_ids=("task-1", "task-2"),
        )
        await repo.save(project)
        fetched = await repo.get(sid("proj-001"))
        assert fetched is not None
        assert fetched.team == ("agent-1", "agent-2", "agent-3")
        assert fetched.task_ids == ("task-1", "task-2")

    async def test_roundtrip_preserves_deadline_and_budget(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project(
            deadline="2026-12-31T23:59:59",
            budget=1500.50,
        )
        await repo.save(project)
        fetched = await repo.get(sid("proj-001"))
        assert fetched is not None
        assert fetched.deadline == "2026-12-31T23:59:59"
        assert fetched.budget == 1500.50

    async def test_roundtrip_preserves_none_lead_and_deadline(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project(lead=None, deadline=None)
        await repo.save(project)
        fetched = await repo.get(sid("proj-001"))
        assert fetched is not None
        assert fetched.lead is None
        assert fetched.deadline is None

    async def test_empty_team_and_task_ids(self, repo: SQLiteProjectRepository) -> None:
        project = _make_project(team=(), task_ids=())
        await repo.save(project)
        fetched = await repo.get(sid("proj-001"))
        assert fetched is not None
        assert fetched.team == ()
        assert fetched.task_ids == ()

    async def test_create_inserts_then_rejects_duplicate(
        self, repo: SQLiteProjectRepository
    ) -> None:
        await repo.create(_make_project(project_id="p-dup"))
        fetched = await repo.get(sid("p-dup"))
        assert fetched is not None
        with pytest.raises(DuplicateRecordError):
            await repo.create(_make_project(project_id="p-dup"))

    async def test_update_modifies_then_rejects_missing(
        self, repo: SQLiteProjectRepository
    ) -> None:
        original = _make_project(project_id="p-up", name="Original")
        await repo.create(original)
        renamed = original.model_copy(update={"name": "Renamed"})
        await repo.update(renamed)
        fetched = await repo.get(sid("p-up"))
        assert fetched is not None
        assert fetched.name == "Renamed"
        with pytest.raises(RecordNotFoundError):
            await repo.update(_make_project(project_id="p-ghost"))

    async def test_list_items_and_query_empty(
        self, repo: SQLiteProjectRepository
    ) -> None:
        assert await repo.list_items() == ()
        assert await repo.query(ProjectFilterSpec()) == ()


@pytest.mark.unit
class TestSQLiteProjectRepositoryErrorPaths:
    """DB failures in every method translate to ``QueryError``."""

    @pytest.fixture
    def repo(self, migrated_db: aiosqlite.Connection) -> SQLiteProjectRepository:
        return SQLiteProjectRepository(
            migrated_db, write_context=make_private_write_context()
        )

    async def test_create_translates_db_error(
        self, repo: SQLiteProjectRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.create(_make_project())

    async def test_update_translates_db_error(
        self, repo: SQLiteProjectRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.update(_make_project())

    async def test_save_translates_db_error(
        self, repo: SQLiteProjectRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.save(_make_project())

    async def test_get_translates_db_error(self, repo: SQLiteProjectRepository) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.get(sid("proj-001"))

    async def test_delete_translates_db_error(
        self, repo: SQLiteProjectRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.delete(sid("proj-001"))

    async def test_list_items_translates_db_error(
        self, repo: SQLiteProjectRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.list_items(limit=10, offset=0)

    async def test_query_translates_db_error(
        self, repo: SQLiteProjectRepository
    ) -> None:
        with (
            patch.object(repo._db, "execute", side_effect=aiosqlite.Error("boom")),
            pytest.raises(QueryError),
        ):
            await repo.query(ProjectFilterSpec(), limit=10, offset=0)
