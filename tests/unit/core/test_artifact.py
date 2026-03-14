"""Tests for artifact domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.artifact import Artifact, ExpectedArtifact
from synthorg.core.enums import ArtifactType

pytestmark = pytest.mark.timeout(30)

# ── ExpectedArtifact ─────────────────────────────────────────────


@pytest.mark.unit
class TestExpectedArtifact:
    def test_valid_construction(self) -> None:
        ea = ExpectedArtifact(type=ArtifactType.CODE, path="src/auth/")
        assert ea.type is ArtifactType.CODE
        assert ea.path == "src/auth/"

    def test_all_artifact_types_accepted(self) -> None:
        for art_type in ArtifactType:
            ea = ExpectedArtifact(type=art_type, path="some/path")
            assert ea.type is art_type

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpectedArtifact(type=ArtifactType.CODE, path="")

    def test_whitespace_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            ExpectedArtifact(type=ArtifactType.CODE, path="   ")

    def test_frozen(self) -> None:
        ea = ExpectedArtifact(type=ArtifactType.CODE, path="src/")
        with pytest.raises(ValidationError):
            ea.path = "other/"  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        ea = ExpectedArtifact(type=ArtifactType.TESTS, path="tests/auth/")
        json_str = ea.model_dump_json()
        restored = ExpectedArtifact.model_validate_json(json_str)
        assert restored == ea

    def test_factory(self) -> None:
        from tests.unit.core.conftest import ExpectedArtifactFactory

        ea = ExpectedArtifactFactory.build()
        assert isinstance(ea, ExpectedArtifact)
        assert isinstance(ea.type, ArtifactType)
        assert len(ea.path) >= 1


# ── Artifact ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestArtifact:
    def test_valid_construction(self) -> None:
        now = datetime.now(tz=UTC)
        artifact = Artifact(
            id="artifact-001",
            type=ArtifactType.CODE,
            path="src/auth/login.py",
            task_id="task-123",
            created_by="sarah_chen",
            description="Login endpoint implementation",
            created_at=now,
        )
        assert artifact.id == "artifact-001"
        assert artifact.type is ArtifactType.CODE
        assert artifact.path == "src/auth/login.py"
        assert artifact.task_id == "task-123"
        assert artifact.created_by == "sarah_chen"
        assert artifact.description == "Login endpoint implementation"
        assert artifact.created_at == now

    def test_defaults(self) -> None:
        artifact = Artifact(
            id="artifact-002",
            type=ArtifactType.DOCUMENTATION,
            path="docs/api/auth.md",
            task_id="task-123",
            created_by="sarah_chen",
        )
        assert artifact.description == ""
        assert artifact.created_at is None

    def test_whitespace_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Artifact(
                id="   ",
                type=ArtifactType.CODE,
                path="src/file.py",
                task_id="task-1",
                created_by="agent-1",
            )

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Artifact(
                id="",
                type=ArtifactType.CODE,
                path="src/file.py",
                task_id="task-1",
                created_by="agent-1",
            )

    def test_whitespace_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Artifact(
                id="artifact-1",
                type=ArtifactType.CODE,
                path="   ",
                task_id="task-1",
                created_by="agent-1",
            )

    def test_whitespace_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Artifact(
                id="artifact-1",
                type=ArtifactType.CODE,
                path="src/file.py",
                task_id="   ",
                created_by="agent-1",
            )

    def test_whitespace_created_by_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Artifact(
                id="artifact-1",
                type=ArtifactType.CODE,
                path="src/file.py",
                task_id="task-1",
                created_by="   ",
            )

    def test_created_at_accepts_datetime(self) -> None:
        now = datetime.now(tz=UTC)
        artifact = Artifact(
            id="artifact-1",
            type=ArtifactType.CODE,
            path="src/file.py",
            task_id="task-1",
            created_by="agent-1",
            created_at=now,
        )
        assert artifact.created_at == now

    def test_created_at_none_allowed(self) -> None:
        artifact = Artifact(
            id="artifact-1",
            type=ArtifactType.CODE,
            path="src/file.py",
            task_id="task-1",
            created_by="agent-1",
            created_at=None,
        )
        assert artifact.created_at is None

    def test_frozen(self) -> None:
        artifact = Artifact(
            id="artifact-1",
            type=ArtifactType.CODE,
            path="src/file.py",
            task_id="task-1",
            created_by="agent-1",
        )
        with pytest.raises(ValidationError):
            artifact.id = "other"  # type: ignore[misc]

    def test_factory(self) -> None:
        from tests.unit.core.conftest import ArtifactFactory

        artifact = ArtifactFactory.build()
        assert isinstance(artifact, Artifact)
        assert isinstance(artifact.type, ArtifactType)

    def test_json_roundtrip(self) -> None:
        now = datetime.now(tz=UTC)
        artifact = Artifact(
            id="artifact-1",
            type=ArtifactType.TESTS,
            path="tests/auth/",
            task_id="task-123",
            created_by="agent-1",
            description="Auth tests",
            created_at=now,
        )
        json_str = artifact.model_dump_json()
        restored = Artifact.model_validate_json(json_str)
        assert restored.id == artifact.id
        assert restored.type is artifact.type
        assert restored.created_at == artifact.created_at
