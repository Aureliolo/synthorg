"""Tests for the artifact controller's storage-rollback invariant."""

import asyncio
from pathlib import Path

import pytest

from synthorg.api.controllers._artifact_helpers import (
    replaced_content,
    save_metadata_with_rollback,
    store_content,
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


@pytest.mark.unit
class TestStoreContent:
    async def test_records_the_written_size(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        saved: list[Artifact] = []

        async def _save(artifact: Artifact) -> None:
            saved.append(artifact)

        updated = await store_content(
            mock_of[ArtifactService](save=_save),
            storage,
            _artifact("art-1"),
            b"payload",
        )

        assert updated.size_bytes == len(b"payload")
        assert updated.content_type == "application/octet-stream"
        assert await storage.retrieve("art-1") == b"payload"
        assert saved == [updated]

    async def test_concurrent_uploads_leave_storage_agreeing_with_metadata(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        # Interleaved, one upload's rollback would restore content the other
        # had already superseded, against metadata describing that other
        # upload. Each upload holds the artifact's lock end to end instead.
        saved: list[Artifact] = []
        started = asyncio.Event()

        async def _slow_save(artifact: Artifact) -> None:
            started.set()
            await asyncio.sleep(0)
            saved.append(artifact)

        service = mock_of[ArtifactService](save=_slow_save)
        first = asyncio.create_task(
            store_content(service, storage, _artifact("art-1"), b"first")
        )
        await started.wait()
        second = asyncio.create_task(
            store_content(service, storage, _artifact("art-1"), b"second-payload")
        )
        await asyncio.gather(first, second)

        # Whichever upload committed last owns both halves.
        assert saved[-1].size_bytes == len(await storage.retrieve("art-1"))

    async def test_a_failed_save_releases_the_lock(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(RuntimeError):
            await store_content(
                _failing_service(), storage, _artifact("art-1"), b"payload"
            )

        # A lock the failure path never released would hang this call rather
        # than fail it.
        with pytest.raises(RuntimeError):
            await store_content(
                _failing_service(), storage, _artifact("art-1"), b"payload"
            )
