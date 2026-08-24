"""Tests for async task state channel on AgentContext."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.async_tasks.models import (
    AsyncTaskRecord,
    AsyncTaskStateChannel,
    AsyncTaskStatus,
)
from synthorg.core.agent import AgentIdentity
from synthorg.engine.compaction.models import CompressionMetadata
from synthorg.engine.context import AgentContext
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ZERO_TOKEN_USAGE, ChatMessage


def _make_record(**overrides: object) -> AsyncTaskRecord:
    defaults: dict[str, object] = {
        "task_id": "task-1",
        "agent_name": "worker",
        "status": AsyncTaskStatus.RUNNING,
        "created_at": datetime(2026, 4, 14, tzinfo=UTC),
        "updated_at": datetime(2026, 4, 14, tzinfo=UTC),
    }
    defaults.update(overrides)
    return AsyncTaskRecord(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestAgentContextAsyncTaskState:
    """AsyncTaskStateChannel field on AgentContext."""

    def test_default_is_empty_channel(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        assert isinstance(ctx.async_task_state, AsyncTaskStateChannel)
        assert ctx.async_task_state.records == ()

    def test_with_async_task_state_helper(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        record = _make_record()
        new_channel = ctx.async_task_state.with_record(record)
        ctx2 = ctx.with_async_task_state(new_channel)
        assert len(ctx2.async_task_state.records) == 1
        assert ctx2.async_task_state.records[0].task_id == "task-1"
        # Original unchanged
        assert len(ctx.async_task_state.records) == 0

    def test_survives_model_copy(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        record = _make_record()
        channel = ctx.async_task_state.with_record(record)
        ctx2 = ctx.model_copy(update={"async_task_state": channel})
        assert len(ctx2.async_task_state.records) == 1

    def test_survives_with_compression(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        """State channel must not be touched by compaction."""
        ctx = AgentContext.from_identity(sample_agent)
        record = _make_record()
        channel = ctx.async_task_state.with_record(record)
        ctx_with_task = ctx.model_copy(
            update={"async_task_state": channel},
        )
        metadata = CompressionMetadata(
            compression_point=5,
            archived_turns=5,
            summary_tokens=200,
        )
        ctx_compressed = ctx_with_task.with_compression(
            metadata=metadata,
            compressed_conversation=(),
            fill_tokens=100,
        )
        # State channel preserved through compression
        assert len(ctx_compressed.async_task_state.records) == 1
        assert ctx_compressed.async_task_state.records[0].task_id == "task-1"

    def test_from_identity_factory_includes_field(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent,
            max_turns=10,
        )
        assert hasattr(ctx, "async_task_state")
        assert isinstance(ctx.async_task_state, AsyncTaskStateChannel)

    def test_to_snapshot_still_works(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        record = _make_record()
        channel = ctx.async_task_state.with_record(record)
        ctx2 = ctx.model_copy(update={"async_task_state": channel})
        snapshot = ctx2.to_snapshot()
        assert snapshot.execution_id == ctx2.execution_id

    def test_with_turn_completed_preserves_state(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        record = _make_record()
        channel = ctx.async_task_state.with_record(record)
        ctx2 = ctx.model_copy(update={"async_task_state": channel})
        msg = ChatMessage(role=MessageRole.ASSISTANT, content="Hello")
        ctx3 = ctx2.with_turn_completed(ZERO_TOKEN_USAGE, msg)
        assert len(ctx3.async_task_state.records) == 1
