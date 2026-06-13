"""Tests for SQLiteArtifactRepository."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.sqlite.artifact_repo import SQLiteArtifactRepository
from tests._shared.persistence import make_private_write_context


def _make_artifact(  # noqa: PLR0913
    *,
    artifact_id: str = "artifact-001",
    artifact_type: ArtifactType = ArtifactType.CODE,
    path: str = "src/auth/login.py",
    task_id: str = "task-123",
    created_by: str = "agent-1",
    description: str = "Login endpoint",
    content_type: str = "",
    size_bytes: int = 0,
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> Artifact:
    return Artifact(
        id=artifact_id,
        type=artifact_type,
        path=path,
        task_id=task_id,
        created_by=created_by,
        description=description,
        content_type=content_type,
        size_bytes=size_bytes,
        created_at=created_at,
    )


@pytest.fixture
def repo(migrated_db: aiosqlite.Connection) -> SQLiteArtifactRepository:
    return SQLiteArtifactRepository(
        migrated_db, write_context=make_private_write_context()
    )


@pytest.mark.unit
class TestSQLiteArtifactRepository:
    async def test_save_and_get(self, repo: SQLiteArtifactRepository) -> None:
        artifact = _make_artifact()
        await repo.save(artifact)
        fetched = await repo.get("artifact-001")
        assert fetched is not None
        assert fetched.id == "artifact-001"
        assert fetched.type is ArtifactType.CODE
        assert fetched.path == "src/auth/login.py"
        assert fetched.task_id == "task-123"
        assert fetched.created_by == "agent-1"
        assert fetched.description == "Login endpoint"

    async def test_get_returns_none_for_missing(
        self, repo: SQLiteArtifactRepository
    ) -> None:
        result = await repo.get("nonexistent")
        assert result is None

    async def test_save_upsert_updates_existing(
        self, repo: SQLiteArtifactRepository
    ) -> None:
        artifact = _make_artifact()
        await repo.save(artifact)
        updated = artifact.model_copy(update={"description": "Updated desc"})
        await repo.save(updated)
        fetched = await repo.get("artifact-001")
        assert fetched is not None
        assert fetched.description == "Updated desc"

    async def test_list_items_returns_all(self, repo: SQLiteArtifactRepository) -> None:
        await repo.save(_make_artifact(artifact_id="a1"))
        await repo.save(_make_artifact(artifact_id="a2"))
        result = await repo.list_items()
        assert len(result) == 2

    async def test_query_filter_by_task_id(
        self, repo: SQLiteArtifactRepository
    ) -> None:
        await repo.save(_make_artifact(artifact_id="a1", task_id="task-1"))
        await repo.save(_make_artifact(artifact_id="a2", task_id="task-2"))
        result = await repo.query(
            ArtifactFilterSpec(task_id="task-1"),
        )
        assert len(result) == 1
        assert result[0].task_id == "task-1"

    async def test_query_filter_by_created_by(
        self,
        repo: SQLiteArtifactRepository,
    ) -> None:
        await repo.save(_make_artifact(artifact_id="a1", created_by="alice"))
        await repo.save(_make_artifact(artifact_id="a2", created_by="bob"))
        result = await repo.query(
            ArtifactFilterSpec(created_by="alice"),
        )
        assert len(result) == 1
        assert result[0].created_by == "alice"

    async def test_query_filter_by_type(self, repo: SQLiteArtifactRepository) -> None:
        await repo.save(
            _make_artifact(artifact_id="a1", artifact_type=ArtifactType.CODE)
        )
        await repo.save(
            _make_artifact(artifact_id="a2", artifact_type=ArtifactType.TESTS)
        )
        result = await repo.query(
            ArtifactFilterSpec(artifact_type=ArtifactType.TESTS),
        )
        assert len(result) == 1
        assert result[0].type is ArtifactType.TESTS

    async def test_query_combined_filters(self, repo: SQLiteArtifactRepository) -> None:
        await repo.save(
            _make_artifact(artifact_id="a1", task_id="task-1", created_by="alice")
        )
        await repo.save(
            _make_artifact(artifact_id="a2", task_id="task-1", created_by="bob")
        )
        await repo.save(
            _make_artifact(artifact_id="a3", task_id="task-2", created_by="alice")
        )
        result = await repo.query(
            ArtifactFilterSpec(task_id="task-1", created_by="alice"),
        )
        assert len(result) == 1
        assert result[0].id == "a1"

    async def test_count(self, repo: SQLiteArtifactRepository) -> None:
        await repo.save(_make_artifact(artifact_id="a1", task_id="task-1"))
        await repo.save(_make_artifact(artifact_id="a2", task_id="task-1"))
        await repo.save(_make_artifact(artifact_id="a3", task_id="task-2"))
        count = await repo.count(ArtifactFilterSpec(task_id="task-1"))
        assert count == 2

    async def test_delete_existing(self, repo: SQLiteArtifactRepository) -> None:
        await repo.save(_make_artifact())
        deleted = await repo.delete("artifact-001")
        assert deleted is True
        assert await repo.get("artifact-001") is None

    async def test_delete_missing(self, repo: SQLiteArtifactRepository) -> None:
        deleted = await repo.delete("nonexistent")
        assert deleted is False

    async def test_roundtrip_preserves_content_fields(
        self, repo: SQLiteArtifactRepository
    ) -> None:
        artifact = _make_artifact(
            content_type="application/pdf",
            size_bytes=1048576,
        )
        await repo.save(artifact)
        fetched = await repo.get("artifact-001")
        assert fetched is not None
        assert fetched.content_type == "application/pdf"
        assert fetched.size_bytes == 1048576

    async def test_roundtrip_preserves_created_at(
        self, repo: SQLiteArtifactRepository
    ) -> None:
        now = datetime.now(UTC)
        artifact = _make_artifact(created_at=now)
        await repo.save(artifact)
        fetched = await repo.get("artifact-001")
        assert fetched is not None
        assert fetched.created_at == now

    async def test_created_at_always_aware(
        self, repo: SQLiteArtifactRepository
    ) -> None:
        """created_at is NOT NULL and always timezone-aware on roundtrip."""
        ts = datetime(2026, 3, 31, 12, 0, 0, tzinfo=UTC)
        artifact = Artifact(
            id="a-aware",
            type=ArtifactType.CODE,
            path="src/x.py",
            task_id="task-1",
            created_by="agent-1",
            created_at=ts,
        )
        await repo.save(artifact)
        fetched = await repo.get("a-aware")
        assert fetched is not None
        assert fetched.created_at is not None
        assert fetched.created_at.tzinfo is not None
        assert fetched.created_at == ts

    async def test_naive_created_at_rejected(
        self,
        repo: SQLiteArtifactRepository,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        """A stored naive timestamp is rejected on read, not silently coerced."""
        await repo.save(_make_artifact(artifact_id="a-naive"))
        await migrated_db.execute(
            "UPDATE artifacts SET created_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00", "a-naive"),
        )
        await migrated_db.commit()
        with pytest.raises(QueryError):
            await repo.get("a-naive")
