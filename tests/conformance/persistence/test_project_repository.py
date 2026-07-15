"""Conformance tests for ``ProjectRepository`` (SQLite + Postgres)."""

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.project_protocol import ProjectFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


def _project(
    *,
    project_id: str = "proj-001",
    name: str = "Test Project",
    status: ProjectStatus = ProjectStatus.PLANNING,
    lead: str | None = None,
) -> Project:
    return Project(
        id=as_uuid(project_id),
        name=NotBlankStr(name),
        description="A test project",
        lead=NotBlankStr(lead) if lead else None,
        status=status,
    )


class TestProjectRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())

        fetched = await backend.projects.get(NotBlankStr(sid("proj-001")))
        assert fetched is not None
        assert fetched.id == as_uuid("proj-001")
        assert fetched.name == "Test Project"
        assert fetched.status is ProjectStatus.PLANNING

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.projects.get(NotBlankStr("ghost")) is None

    async def test_autonomy_mode_default_is_none(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project(project_id="proj-inherit"))
        inherited = await backend.projects.get(NotBlankStr(sid("proj-inherit")))
        assert inherited is not None
        assert inherited.autonomy_mode is None

    @pytest.mark.parametrize(
        "mode",
        [AutonomyLevel.LOCKED, AutonomyLevel.SUPERVISED, AutonomyLevel.FULL],
        ids=["locked", "supervised", "full"],
    )
    async def test_autonomy_mode_set_round_trips(
        self, backend: PersistenceBackend, mode: AutonomyLevel
    ) -> None:
        # Every operator-set tier round-trips as its enum value, including
        # the gate-off ``full`` value.
        project = _project(project_id="proj-mode").model_copy(
            update={"autonomy_mode": mode},
        )
        await backend.projects.save(project)
        fetched = await backend.projects.get(NotBlankStr(sid("proj-mode")))
        assert fetched is not None
        assert fetched.autonomy_mode is mode

    async def test_autonomy_mode_clear_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        project = _project(project_id="proj-clear").model_copy(
            update={"autonomy_mode": AutonomyLevel.FULL},
        )
        await backend.projects.save(project)
        fetched = await backend.projects.get(NotBlankStr(sid("proj-clear")))
        assert fetched is not None
        cleared = fetched.model_copy(
            update={"autonomy_mode": None, "version": fetched.version + 1},
        )
        await backend.projects.update(cleared)
        after = await backend.projects.get(NotBlankStr(sid("proj-clear")))
        assert after is not None
        assert after.autonomy_mode is None

    async def test_save_upsert(self, backend: PersistenceBackend) -> None:
        p = _project()
        await backend.projects.save(p)

        updated = p.model_copy(update={"name": NotBlankStr("Renamed")})
        await backend.projects.save(updated)

        fetched = await backend.projects.get(NotBlankStr(sid("proj-001")))
        assert fetched is not None
        assert fetched.name == "Renamed"

    async def test_list_items_in_id_order(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project(project_id="p1"))
        await backend.projects.save(_project(project_id="p2"))

        rows = await backend.projects.list_items()
        expected = sorted([as_uuid("p1"), as_uuid("p2")])
        ids = [r.id for r in rows if r.id in expected]
        assert ids == expected

    async def test_query_filter_by_status(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(
            _project(project_id="active", status=ProjectStatus.ACTIVE),
        )
        await backend.projects.save(
            _project(project_id="planning", status=ProjectStatus.PLANNING),
        )

        rows = await backend.projects.query(
            ProjectFilterSpec(status=ProjectStatus.ACTIVE),
        )
        ids = {r.id for r in rows}
        assert as_uuid("active") in ids
        assert as_uuid("planning") not in ids

    async def test_query_filter_by_lead(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project(project_id="alpha", lead="alice"))
        await backend.projects.save(_project(project_id="beta", lead="bob"))

        rows = await backend.projects.query(
            ProjectFilterSpec(lead=NotBlankStr("alice")),
        )
        assert [r.id for r in rows] == [as_uuid("alpha")]

    async def test_query_respects_limit(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.projects.save(_project(project_id=f"p-{i:02d}"))

        rows = await backend.projects.query(
            ProjectFilterSpec(),
            limit=3,
        )
        assert len(rows) == 3

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())

        deleted = await backend.projects.delete(NotBlankStr(sid("proj-001")))
        assert deleted is True
        assert await backend.projects.get(NotBlankStr(sid("proj-001"))) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.projects.delete(NotBlankStr("ghost")) is False

    async def test_create_inserts_new_row(self, backend: PersistenceBackend) -> None:
        await backend.projects.create(_project(project_id="p-create"))

        fetched = await backend.projects.get(NotBlankStr(sid("p-create")))
        assert fetched is not None
        assert fetched.id == as_uuid("p-create")

    async def test_create_rejects_duplicate(self, backend: PersistenceBackend) -> None:
        await backend.projects.create(_project(project_id="p-dup"))

        with pytest.raises(DuplicateRecordError):
            await backend.projects.create(_project(project_id="p-dup"))

    async def test_update_modifies_existing_row(
        self, backend: PersistenceBackend
    ) -> None:
        original = _project(project_id="p-up", name="Original")
        await backend.projects.create(original)

        renamed = original.model_copy(update={"name": NotBlankStr("Renamed")})
        await backend.projects.update(renamed)

        fetched = await backend.projects.get(NotBlankStr(sid("p-up")))
        assert fetched is not None
        assert fetched.name == "Renamed"

    async def test_update_rejects_missing(self, backend: PersistenceBackend) -> None:
        with pytest.raises(RecordNotFoundError):
            await backend.projects.update(_project(project_id="p-ghost"))

    async def test_version_round_trips(self, backend: PersistenceBackend) -> None:
        stamped = _project(project_id="p-ver").model_copy(update={"version": 3})
        await backend.projects.create(stamped)

        fetched = await backend.projects.get(NotBlankStr(sid("p-ver")))
        assert fetched is not None
        assert fetched.version == 3

    async def test_version_guarded_update_conflict(
        self, backend: PersistenceBackend
    ) -> None:
        # A stale writer whose expected_version no longer matches the stored
        # row is rejected rather than clobbering the concurrent update.
        original = _project(project_id="p-cas")
        await backend.projects.create(original)

        winner = original.model_copy(
            update={"lead": NotBlankStr("alice"), "version": 2}
        )
        await backend.projects.update(winner, expected_version=1)

        loser = original.model_copy(update={"lead": NotBlankStr("bob"), "version": 2})
        with pytest.raises(PersistenceVersionConflictError):
            await backend.projects.update(loser, expected_version=1)

        fetched = await backend.projects.get(NotBlankStr(sid("p-cas")))
        assert fetched is not None
        assert fetched.lead == "alice"
        assert fetched.version == winner.version

    async def test_list_items_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.projects.list_items() == ()

    @pytest.mark.parametrize(
        ("limit", "offset"),
        [(0, 0), (-1, 0), (1, -1)],
    )
    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend, limit: int, offset: int
    ) -> None:
        with pytest.raises(QueryError):
            await backend.projects.list_items(limit=limit, offset=offset)

    @pytest.mark.parametrize(
        ("limit", "offset"),
        [(0, 0), (-1, 0), (1, -1)],
    )
    async def test_query_rejects_invalid_pagination(
        self, backend: PersistenceBackend, limit: int, offset: int
    ) -> None:
        with pytest.raises(QueryError):
            await backend.projects.query(
                ProjectFilterSpec(), limit=limit, offset=offset
            )
