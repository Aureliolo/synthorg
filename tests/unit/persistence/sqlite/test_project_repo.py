"""Tests for SQLiteProjectRepository."""

import aiosqlite
import pytest

from synthorg.core.enums import ProjectStatus
from synthorg.core.project import Project
from synthorg.persistence.project_protocol import ProjectFilterSpec
from synthorg.persistence.sqlite.project_repo import SQLiteProjectRepository
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
        id=project_id,
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
        fetched = await repo.get("proj-001")
        assert fetched is not None
        assert fetched.id == "proj-001"
        assert fetched.name == "Test Project"
        assert fetched.description == "A test project"
        assert fetched.status is ProjectStatus.PLANNING

    async def test_get_returns_none_for_missing(
        self, repo: SQLiteProjectRepository
    ) -> None:
        result = await repo.get("nonexistent")
        assert result is None

    async def test_save_upsert_updates_existing(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project()
        await repo.save(project)
        updated = project.model_copy(update={"name": "Updated Name"})
        await repo.save(updated)
        fetched = await repo.get("proj-001")
        assert fetched is not None
        assert fetched.name == "Updated Name"

    async def test_list_items_in_id_order(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(_make_project(project_id="p1", name="P1"))
        await repo.save(_make_project(project_id="p2", name="P2"))
        result = await repo.list_items()
        assert len(result) == 2
        assert result[0].id == "p1"
        assert result[1].id == "p2"

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
        assert result[0].id == "p1"

    async def test_delete_existing(self, repo: SQLiteProjectRepository) -> None:
        await repo.save(_make_project())
        deleted = await repo.delete("proj-001")
        assert deleted is True
        assert await repo.get("proj-001") is None

    async def test_delete_missing(self, repo: SQLiteProjectRepository) -> None:
        deleted = await repo.delete("nonexistent")
        assert deleted is False

    async def test_roundtrip_preserves_team_and_task_ids(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project(
            team=("agent-1", "agent-2", "agent-3"),
            task_ids=("task-1", "task-2"),
        )
        await repo.save(project)
        fetched = await repo.get("proj-001")
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
        fetched = await repo.get("proj-001")
        assert fetched is not None
        assert fetched.deadline == "2026-12-31T23:59:59"
        assert fetched.budget == 1500.50

    async def test_roundtrip_preserves_none_lead_and_deadline(
        self, repo: SQLiteProjectRepository
    ) -> None:
        project = _make_project(lead=None, deadline=None)
        await repo.save(project)
        fetched = await repo.get("proj-001")
        assert fetched is not None
        assert fetched.lead is None
        assert fetched.deadline is None

    async def test_empty_team_and_task_ids(self, repo: SQLiteProjectRepository) -> None:
        project = _make_project(team=(), task_ids=())
        await repo.save(project)
        fetched = await repo.get("proj-001")
        assert fetched is not None
        assert fetched.team == ()
        assert fetched.task_ids == ()
