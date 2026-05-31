"""Shared fixtures for project-brain service-level unit tests.

Wires a :class:`ProjectBrainService` over an in-memory memory backend, a fake
append-only repository, real chunker/indexer, and a fake workspace writer, so
the write path (SQL append + index + index-state mark) and the lifecycle helpers
can be exercised without git or a real database.
"""

from collections.abc import AsyncIterator
from typing import cast

import pytest
import pytest_asyncio

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.project_brain.chunker import BrainChunker
from synthorg.project_brain.indexer import BrainIndexer
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.writer import BrainWriter
from tests._shared import FakeClock
from tests.unit.api.fakes import FakeProjectBrainRepository


class FakeBrainWriter:
    """Records snapshot writes; can be toggled to raise ``BrainCommitError``."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, int]] = []
        self.fail = False

    async def write(self, *, project_id: NotBlankStr, entry: object) -> NotBlankStr:
        """Record (or fail) a snapshot write.

        Returns:
            A stub commit hash.

        Raises:
            BrainCommitError: When ``fail`` is set.
        """
        from synthorg.project_brain.errors import BrainCommitError

        if self.fail:
            msg = "snapshot boom"
            raise BrainCommitError(msg)
        self.writes.append(
            (project_id, entry.entry_id, entry.revision)  # type: ignore[attr-defined]
        )
        return NotBlankStr("deadbeefcafe")


@pytest.fixture
def brain_repo() -> FakeProjectBrainRepository:
    """An in-memory append-only brain repository."""
    return FakeProjectBrainRepository()


@pytest.fixture
def brain_writer() -> FakeBrainWriter:
    """A fake workspace writer."""
    return FakeBrainWriter()


@pytest_asyncio.fixture
async def memory_backend() -> AsyncIterator[InMemoryBackend]:
    """A connected in-memory agent-memory backend."""
    backend = InMemoryBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
def brain_service(
    brain_repo: FakeProjectBrainRepository,
    brain_writer: FakeBrainWriter,
    memory_backend: InMemoryBackend,
) -> ProjectBrainService:
    """A :class:`ProjectBrainService` wired over the test doubles."""
    return ProjectBrainService(
        repo=brain_repo,
        workspace_service=cast("object", None),  # type: ignore[arg-type]
        chunker=BrainChunker(),
        indexer=BrainIndexer(backend=memory_backend),
        writer=cast("BrainWriter", brain_writer),
        backend=memory_backend,
        clock=FakeClock(),
    )
