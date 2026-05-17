"""Conformance tests for ``ArtifactRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.artifact import Artifact
from synthorg.core.enums import ArtifactType
from synthorg.core.types import NotBlankStr
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _artifact(  # noqa: PLR0913
    *,
    artifact_id: str = "artifact-001",
    artifact_type: ArtifactType = ArtifactType.CODE,
    task_id: str = "task-123",
    created_by: str = "agent-1",
    path: str = "src/auth/login.py",
    project_id: str | None = None,
) -> Artifact:
    return Artifact(
        id=NotBlankStr(artifact_id),
        type=artifact_type,
        path=NotBlankStr(path),
        task_id=NotBlankStr(task_id),
        created_by=NotBlankStr(created_by),
        description="Login endpoint",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        project_id=NotBlankStr(project_id) if project_id else None,
    )


class TestArtifactRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.artifacts.save(_artifact())

        fetched = await backend.artifacts.get(NotBlankStr("artifact-001"))
        assert fetched is not None
        assert fetched.id == "artifact-001"
        assert fetched.type is ArtifactType.CODE
        assert fetched.task_id == "task-123"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.artifacts.get(NotBlankStr("ghost")) is None

    async def test_save_upsert(self, backend: PersistenceBackend) -> None:
        a = _artifact()
        await backend.artifacts.save(a)

        updated = a.model_copy(update={"description": "refined"})
        await backend.artifacts.save(updated)

        fetched = await backend.artifacts.get(NotBlankStr("artifact-001"))
        assert fetched is not None
        assert fetched.description == "refined"

    async def test_save_returning_outcome_true_on_insert(
        self, backend: PersistenceBackend
    ) -> None:
        """First-write returns ``True`` so the service can audit CREATED."""
        created = await backend.artifacts.save_returning_outcome(
            _artifact(artifact_id="new-row")
        )
        assert created is True

    async def test_save_returning_outcome_false_on_update(
        self, backend: PersistenceBackend
    ) -> None:
        """Second write to the same id returns ``False`` (UPDATED audit)."""
        a = _artifact(artifact_id="existing-row")
        await backend.artifacts.save(a)
        updated = a.model_copy(update={"description": "changed"})

        created = await backend.artifacts.save_returning_outcome(updated)
        assert created is False

    async def test_project_id_round_trips_when_set(
        self, backend: PersistenceBackend
    ) -> None:
        """``project_id`` is persisted and returned by ``get()``."""
        await backend.artifacts.save(
            _artifact(artifact_id="proj-bound", project_id="proj-42"),
        )

        fetched = await backend.artifacts.get(NotBlankStr("proj-bound"))
        assert fetched is not None
        assert fetched.project_id == "proj-42"

    async def test_project_id_round_trips_when_none(
        self, backend: PersistenceBackend
    ) -> None:
        """``project_id`` of ``None`` round-trips as ``None``."""
        await backend.artifacts.save(_artifact(artifact_id="unbound"))

        fetched = await backend.artifacts.get(NotBlankStr("unbound"))
        assert fetched is not None
        assert fetched.project_id is None

    async def test_project_id_update_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        """Updating the artifact persists a changed ``project_id``."""
        a = _artifact(artifact_id="moved", project_id="proj-old")
        await backend.artifacts.save(a)
        moved = a.model_copy(update={"project_id": NotBlankStr("proj-new")})
        await backend.artifacts.save(moved)

        fetched = await backend.artifacts.get(NotBlankStr("moved"))
        assert fetched is not None
        assert fetched.project_id == "proj-new"

    async def test_project_id_clear_to_none_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        """Clearing ``project_id`` to ``None`` persists the unset.

        Pins the regression-prone branch for nullable columns: a repo
        that accidentally preserves the old non-null value on a
        ``NULL`` update (e.g. ``UPDATE ... SET project_id =
        COALESCE(?, project_id)``) would still pass the
        non-null->non-null update test.
        """
        a = _artifact(artifact_id="cleared", project_id="proj-bound")
        await backend.artifacts.save(a)

        cleared = a.model_copy(update={"project_id": None})
        await backend.artifacts.save(cleared)

        fetched = await backend.artifacts.get(NotBlankStr("cleared"))
        assert fetched is not None
        assert fetched.project_id is None

    async def test_list_items_returns_all(self, backend: PersistenceBackend) -> None:
        """list_items returns all artifacts in id order."""
        await backend.artifacts.save(_artifact(artifact_id="a1"))
        await backend.artifacts.save(_artifact(artifact_id="a2"))

        rows = await backend.artifacts.list_items()
        ids = {r.id for r in rows}
        assert {"a1", "a2"} <= ids

    async def test_query_filter_by_task_id(self, backend: PersistenceBackend) -> None:
        """query filters by task_id."""
        await backend.artifacts.save(_artifact(artifact_id="x", task_id="t1"))
        await backend.artifacts.save(_artifact(artifact_id="y", task_id="t2"))

        rows = await backend.artifacts.query(
            ArtifactFilterSpec(task_id=NotBlankStr("t1"))
        )
        assert [r.id for r in rows] == ["x"]

    async def test_query_filter_by_type(self, backend: PersistenceBackend) -> None:
        """query filters by artifact_type."""
        await backend.artifacts.save(
            _artifact(artifact_id="code", artifact_type=ArtifactType.CODE),
        )
        await backend.artifacts.save(
            _artifact(artifact_id="doc", artifact_type=ArtifactType.DOCUMENTATION),
        )

        rows = await backend.artifacts.query(
            ArtifactFilterSpec(artifact_type=ArtifactType.DOCUMENTATION)
        )
        assert [r.id for r in rows] == ["doc"]

    async def test_count(self, backend: PersistenceBackend) -> None:
        """count returns the number of matching artifacts."""
        await backend.artifacts.save(_artifact(artifact_id="a1", task_id="t1"))
        await backend.artifacts.save(_artifact(artifact_id="a2", task_id="t1"))
        await backend.artifacts.save(_artifact(artifact_id="a3", task_id="t2"))

        count = await backend.artifacts.count(
            ArtifactFilterSpec(task_id=NotBlankStr("t1"))
        )
        assert count == 2

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.artifacts.save(_artifact())

        deleted = await backend.artifacts.delete(NotBlankStr("artifact-001"))
        assert deleted is True
        assert await backend.artifacts.get(NotBlankStr("artifact-001")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.artifacts.delete(NotBlankStr("ghost")) is False
