"""The service holds its extractors, it does not clone them.

An extractor is built FROM the memory backend and the tool-invocation tracker,
which are running services holding running :class:`asyncio.Task` objects. A
deep copy of one is not merely wasteful, it is impossible: a task cannot be
pickled, so the copy raises and takes the whole training wiring with it. A live
boot did exactly that, and the only visible consequence was ``eval_loop``
declining for the life of the process on a condition that was not its own.

These pin both halves of the intended guarantee: the caller's mapping cannot be
mutated after the fact, and the values inside it are the very objects the caller
passed.
"""

import asyncio

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import ContentType, TrainingItem
from synthorg.hr.training.protocol import (
    ContentExtractor,
    CurationStrategy,
    SourceSelector,
)
from synthorg.hr.training.service import TrainingService
from synthorg.memory.protocol import MemoryBackend
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class _TaskHoldingExtractor:
    """An extractor shaped like the real ones: it owns a running task.

    ``MemoryBackedExtractor`` reaches a backend whose consolidation loop is a
    live task, so this is the shape the wiring actually passes, reduced to the
    one attribute that decides whether a copy is possible.
    """

    def __init__(self, task: asyncio.Task[None]) -> None:
        self.task = task

    @property
    def content_type(self) -> ContentType:
        """The content type this extractor produces.

        Returns:
            The procedural content type.
        """
        return ContentType.PROCEDURAL

    async def extract(
        self,
        *,
        source_agent_ids: tuple[NotBlankStr, ...],
        new_agent_role: NotBlankStr,
    ) -> tuple[TrainingItem, ...]:
        """Return nothing; the pipeline is not what these tests exercise.

        Returns:
            An empty tuple.
        """
        del source_agent_ids, new_agent_role
        return ()


async def _forever() -> None:
    """Block until cancelled, so the task under test is genuinely running."""
    await asyncio.Event().wait()


def _service(extractors: dict[ContentType, ContentExtractor]) -> TrainingService:
    """Build a service over *extractors* with every other collaborator faked.

    Returns:
        The constructed service.
    """
    return TrainingService(
        selector=mock_of[SourceSelector](),
        extractors=extractors,
        curation=mock_of[CurationStrategy](),
        guards=(),
        memory_backend=mock_of[MemoryBackend](),
    )


class TestLiveCollaborators:
    async def test_an_extractor_holding_a_running_task_is_accepted(self) -> None:
        task = asyncio.create_task(_forever())
        try:
            extractor = _TaskHoldingExtractor(task)
            service = _service({ContentType.PROCEDURAL: extractor})

            assert service._extractors[ContentType.PROCEDURAL] is extractor
        finally:
            task.cancel()

    async def test_the_extractor_is_shared_not_cloned(self) -> None:
        # A cloned memory-backed extractor would read and write a second
        # memory backend, so what a new hire learned would land nowhere the
        # rest of the system looks.
        task = asyncio.create_task(_forever())
        try:
            extractor = _TaskHoldingExtractor(task)
            service = _service({ContentType.SEMANTIC: extractor})

            held = service._extractors[ContentType.SEMANTIC]
            assert isinstance(held, _TaskHoldingExtractor)
            assert held.task is task
        finally:
            task.cancel()

    async def test_the_caller_cannot_change_the_mapping_afterwards(self) -> None:
        task = asyncio.create_task(_forever())
        try:
            first = _TaskHoldingExtractor(task)
            passed: dict[ContentType, ContentExtractor] = {
                ContentType.PROCEDURAL: first
            }
            service = _service(passed)

            passed[ContentType.PROCEDURAL] = _TaskHoldingExtractor(task)

            assert service._extractors[ContentType.PROCEDURAL] is first
        finally:
            task.cancel()
