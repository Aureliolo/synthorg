"""Tests for the artifact controller's storage-rollback invariant."""

from pathlib import Path

import pytest

from synthorg.api.controllers._artifact_helpers import (
    replaced_content,
    save_metadata_with_rollback,
)
from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.engine.artifacts.service import ArtifactService
from synthorg.persistence.filesystem_artifact_storage import (
    FileSystemArtifactStorage,
)
from tests._shared import mock_of


@pytest.fixture
def storage(tmp_path: Path) -> FileSystemArtifactStorage:
    return FileSystemArtifactStorage(
        data_dir=tmp_path,
        max_artifact_bytes=1024,
        max_total_bytes=4096,
    )


def _artifact(artifact_id: str) -> Artifact:
    return Artifact(
        id=artifact_id,
        type=ArtifactType.CODE,
        path="src/example.py",
        task_id="task-1",
        created_by="agent-1",
    )


def _failing_service() -> ArtifactService:
    """A service whose metadata save always fails."""

    async def _save(_artifact: Artifact) -> None:
        msg = "metadata backend unavailable"
        raise RuntimeError(msg)

    service: ArtifactService = mock_of[ArtifactService](save=_save)
    return service


@pytest.mark.unit
class TestReplacedContent:
    async def test_none_when_the_artifact_has_no_content(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        assert await replaced_content(storage, "art-1") is None

    async def test_returns_the_bytes_an_upload_would_overwrite(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        await storage.store("art-1", b"original")
        assert await replaced_content(storage, "art-1") == b"original"


@pytest.mark.unit
class TestSaveMetadataWithRollback:
    async def test_first_upload_leaves_no_orphan_blob(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        await storage.store("art-1", b"uploaded")

        with pytest.raises(RuntimeError):
            await save_metadata_with_rollback(
                _failing_service(),
                storage,
                "art-1",
                _artifact("art-1"),
                previous=None,
            )

        assert await storage.exists("art-1") is False

    async def test_replacement_upload_restores_the_previous_content(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        # The upload the operator is undoing REPLACED content that was
        # already there, so deleting would destroy the bytes the failed
        # write was never authorised to remove.
        await storage.store("art-1", b"original")
        previous = await replaced_content(storage, "art-1")
        await storage.store("art-1", b"replacement")

        with pytest.raises(RuntimeError):
            await save_metadata_with_rollback(
                _failing_service(),
                storage,
                "art-1",
                _artifact("art-1"),
                previous=previous,
            )

        assert await storage.retrieve("art-1") == b"original"

    async def test_successful_save_keeps_the_uploaded_content(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        saved: list[Artifact] = []

        async def _save(artifact: Artifact) -> None:
            saved.append(artifact)

        await storage.store("art-1", b"replacement")
        await save_metadata_with_rollback(
            mock_of[ArtifactService](save=_save),
            storage,
            "art-1",
            _artifact("art-1"),
            previous=b"original",
        )

        assert await storage.retrieve("art-1") == b"replacement"
        assert len(saved) == 1
