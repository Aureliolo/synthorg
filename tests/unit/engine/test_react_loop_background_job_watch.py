"""Loop-level tests for the background-job stall nudge in ReactLoop.

Mirrors ``test_react_loop_steering.py``'s shape: the watcher fires at
the same turn-boundary slot, immediately after ``check_steering``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.background_job_watch import (
    BackgroundJobStalenessConfig,
    BackgroundJobWatcher,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.react_loop import ReactLoop
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse, TokenUsage
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from tests._shared import FakeClock
from tests._shared.fake_background_job_repo import (
    InMemoryBackgroundJobRepository as _InMemoryBackgroundJobRepository,
)

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _stop() -> CompletionResponse:
    return CompletionResponse(
        content="Done.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-model-001",
    )


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    return ctx.with_message(ChatMessage(role=MessageRole.USER, content="Do the task."))


def _is_nudge_msg(msg: ChatMessage) -> bool:
    return msg.role is MessageRole.USER and "job-1" in (msg.content or "")


async def _watcher_with_live_job(
    *, nudge_after_seconds: float = 60.0
) -> BackgroundJobWatcher:
    repo = _InMemoryBackgroundJobRepository()
    registry = BackgroundJobRegistry(repo)
    await registry.save(
        BackgroundJobRecord(
            job_id="job-1",
            container_id="c1",
            owner_id="agent-1:rw",
            command_repr="sleep 300",
            pid=123,
            status=BackgroundJobStatus.RUNNING,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=_START,
            updated_at=_START,
            max_duration_seconds=3600.0,
        )
    )
    config = BackgroundJobStalenessConfig(
        enabled=True, nudge_after_seconds=nudge_after_seconds
    )
    return BackgroundJobWatcher(registry, config)


@pytest.mark.unit
class TestReactLoopBackgroundJobWatch:
    """The loop nudges about a stalled watched job at the turn boundary."""

    async def test_nudged_before_llm_call(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        provider = mock_provider_factory([_stop()])
        clock = FakeClock(start=_START)
        clock.advance(60)
        watcher = await _watcher_with_live_job()

        loop = ReactLoop(background_job_watcher=watcher, clock=clock)
        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason is TerminationReason.COMPLETED
        nudge_msgs = [m for m in result.context.conversation if _is_nudge_msg(m)]
        assert len(nudge_msgs) == 1
        # Injected before the assistant's own response.
        nudge_index = result.context.conversation.index(nudge_msgs[0])
        assistant_index = next(
            i
            for i, m in enumerate(result.context.conversation)
            if m.role is MessageRole.ASSISTANT
        )
        assert nudge_index < assistant_index

    async def test_not_nudged_before_threshold(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context).with_background_job_watched(
            NotBlankStr("job-1"), watching_since=_START
        )
        provider = mock_provider_factory([_stop()])
        clock = FakeClock(start=_START)
        clock.advance(5)
        watcher = await _watcher_with_live_job()

        loop = ReactLoop(background_job_watcher=watcher, clock=clock)
        result = await loop.execute(context=ctx, provider=provider)

        nudge_msgs = [m for m in result.context.conversation if _is_nudge_msg(m)]
        assert nudge_msgs == []

    async def test_no_watcher_is_a_no_op(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop()])

        loop = ReactLoop()
        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason is TerminationReason.COMPLETED
