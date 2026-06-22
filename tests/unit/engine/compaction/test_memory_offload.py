"""Unit tests for the Phase-2 compaction memory offloader."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.compaction.memory_offload import OFFLOAD_TAG, MemoryOffloader
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _archivable() -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(role=MessageRole.USER, content="design the API"),
        ChatMessage(role=MessageRole.ASSISTANT, content="proposed REST endpoints"),
    )


def _entry() -> MemoryEntry:
    return MemoryEntry(
        id=NotBlankStr("mem-1"),
        agent_id=NotBlankStr("agent-1"),
        category=MemoryCategory.PROCEDURAL,
        content=NotBlankStr("user: design the API"),
        metadata=MemoryMetadata(source=NotBlankStr("compaction"), tags=(OFFLOAD_TAG,)),
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )


class TestMemoryOffload:
    async def test_offload_stores_tagged_procedural(self) -> None:
        backend = mock_of[MemoryBackend](
            store=AsyncMock(
                spec=MemoryBackend.store, return_value=NotBlankStr("mem-1")
            ),
        )
        offloader = MemoryOffloader(backend=backend)
        await offloader.offload(
            agent_id=NotBlankStr("agent-1"),
            archivable=_archivable(),
        )
        backend.store.assert_awaited_once()
        request = backend.store.await_args.args[1]
        assert isinstance(request, MemoryStoreRequest)
        assert request.category is MemoryCategory.PROCEDURAL
        assert OFFLOAD_TAG in request.metadata.tags

    async def test_offload_tags_execution_from_context(self) -> None:
        """The ``execution:<id>`` tag is read from the ambient identity."""
        from synthorg.core.execution_identity import (
            ExecutionIdentity,
            execution_identity_scope,
        )

        backend = mock_of[MemoryBackend](
            store=AsyncMock(
                spec=MemoryBackend.store, return_value=NotBlankStr("mem-1")
            ),
        )
        offloader = MemoryOffloader(backend=backend)
        identity = ExecutionIdentity(
            execution_id=NotBlankStr("exec-42"),
            task_id=NotBlankStr("task-1"),
        )
        with execution_identity_scope(identity):
            await offloader.offload(
                agent_id=NotBlankStr("agent-1"),
                archivable=_archivable(),
            )
        request = backend.store.await_args.args[1]
        assert NotBlankStr("execution:exec-42") in request.metadata.tags

    async def test_offload_empty_batch_is_noop(self) -> None:
        backend = mock_of[MemoryBackend](
            store=AsyncMock(spec=MemoryBackend.store),
        )
        offloader = MemoryOffloader(backend=backend)
        await offloader.offload(
            agent_id=NotBlankStr("agent-1"),
            archivable=(ChatMessage(role=MessageRole.ASSISTANT, content="   "),),
        )
        backend.store.assert_not_awaited()

    async def test_offload_failure_is_swallowed(self) -> None:
        backend = mock_of[MemoryBackend](
            store=AsyncMock(
                spec=MemoryBackend.store, side_effect=RuntimeError("memory down")
            ),
        )
        offloader = MemoryOffloader(backend=backend)
        # Must not raise -- offload is best-effort.
        await offloader.offload(
            agent_id=NotBlankStr("agent-1"),
            archivable=_archivable(),
        )

    async def test_rehydrate_returns_offloaded_entries(self) -> None:
        backend = mock_of[MemoryBackend](
            retrieve=AsyncMock(spec=MemoryBackend.retrieve, return_value=(_entry(),)),
        )
        offloader = MemoryOffloader(backend=backend)
        entries = await offloader.rehydrate(agent_id=NotBlankStr("agent-1"))
        assert len(entries) == 1
        assert OFFLOAD_TAG in entries[0].metadata.tags
