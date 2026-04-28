"""Tests for FileSystemArtifactStorage."""

from pathlib import Path

import pytest

from synthorg.core.persistence_errors import (
    ArtifactStorageFullError,
    ArtifactTooLargeError,
    RecordNotFoundError,
)
from synthorg.persistence.filesystem_artifact_storage import (
    FileSystemArtifactStorage,
)


@pytest.fixture
def storage(tmp_path: Path) -> FileSystemArtifactStorage:
    return FileSystemArtifactStorage(
        data_dir=tmp_path,
        max_artifact_bytes=1024,  # 1 KB for testing
        max_total_bytes=4096,  # 4 KB for testing
    )


@pytest.mark.unit
class TestFileSystemArtifactStorage:
    async def test_store_and_retrieve(self, storage: FileSystemArtifactStorage) -> None:
        content = b"hello world"
        await storage.store("art-1", content)
        retrieved = await storage.retrieve("art-1")
        assert retrieved == content

    async def test_store_returns_size(self, storage: FileSystemArtifactStorage) -> None:
        content = b"test data"
        written = await storage.store("art-1", content)
        assert written == len(content)

    async def test_exists_true_after_store(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        await storage.store("art-1", b"data")
        assert await storage.exists("art-1") is True

    async def test_exists_false_before_store(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        assert await storage.exists("art-1") is False

    async def test_delete_existing(self, storage: FileSystemArtifactStorage) -> None:
        await storage.store("art-1", b"data")
        deleted = await storage.delete("art-1")
        assert deleted is True
        assert await storage.exists("art-1") is False

    async def test_delete_missing(self, storage: FileSystemArtifactStorage) -> None:
        deleted = await storage.delete("nonexistent")
        assert deleted is False

    async def test_total_size_accumulates(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        await storage.store("art-1", b"aaa")
        await storage.store("art-2", b"bbbb")
        total = await storage.total_size()
        assert total == 7

    async def test_total_size_decreases_after_delete(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        await storage.store("art-1", b"aaa")
        await storage.store("art-2", b"bbbb")
        await storage.delete("art-1")
        total = await storage.total_size()
        assert total == 4

    async def test_store_rejects_oversized_artifact(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        oversized = b"x" * 2048  # exceeds 1 KB limit
        with pytest.raises(ArtifactTooLargeError):
            await storage.store("art-1", oversized)

    async def test_store_rejects_when_storage_full(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        # Fill up to near capacity (4 KB total limit)
        await storage.store("art-1", b"x" * 1000)
        await storage.store("art-2", b"x" * 1000)
        await storage.store("art-3", b"x" * 1000)
        await storage.store("art-4", b"x" * 1000)
        # This should push over the 4096 byte limit
        with pytest.raises(ArtifactStorageFullError):
            await storage.store("art-5", b"x" * 200)

    async def test_retrieve_missing_raises(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(RecordNotFoundError):
            await storage.retrieve("nonexistent")

    async def test_backend_name(self, storage: FileSystemArtifactStorage) -> None:
        assert storage.backend_name == "filesystem"

    async def test_store_overwrites_existing(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        await storage.store("art-1", b"original")
        await storage.store("art-1", b"updated")
        retrieved = await storage.retrieve("art-1")
        assert retrieved == b"updated"

    async def test_empty_content(self, storage: FileSystemArtifactStorage) -> None:
        written = await storage.store("art-1", b"")
        assert written == 0
        retrieved = await storage.retrieve("art-1")
        assert retrieved == b""

    async def test_total_size_empty(self, storage: FileSystemArtifactStorage) -> None:
        total = await storage.total_size()
        assert total == 0

    async def test_path_traversal_rejected_store(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.store("../../../etc/passwd", b"exploit")

    async def test_path_traversal_rejected_retrieve(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.retrieve("../../secret")

    async def test_path_traversal_rejected_delete(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.delete("../outside")

    async def test_path_traversal_rejected_exists(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.exists("../../etc/shadow")

    async def test_absolute_path_rejected_store(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.store("/etc/passwd", b"exploit")

    async def test_absolute_path_rejected_retrieve(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.retrieve("/etc/passwd")

    async def test_absolute_path_rejected_delete(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.delete("/etc/passwd")

    async def test_absolute_path_rejected_exists(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact_id"):
            await storage.exists("/etc/passwd")
