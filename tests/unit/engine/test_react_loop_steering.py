"""Loop-level tests for steering adoption and task cancellation in ReactLoop."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import InterventionKind, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.models import ActiveSteeringDirective
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.react_loop import ReactLoop
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse, TokenUsage

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

_DIRECTIVE_TEXT = "use Postgres not Mongo"
_DIRECTIVE = ActiveSteeringDirective(
    entry_id=NotBlankStr("dir-1"),
    kind=InterventionKind.REDIRECT,
    text=NotBlankStr(_DIRECTIVE_TEXT),
    author=NotBlankStr("mission-control"),
    recorded_at=datetime(2026, 5, 31, tzinfo=UTC),
)


def _is_steering_msg(msg: ChatMessage) -> bool:
    return msg.role is MessageRole.USER and _DIRECTIVE_TEXT in (msg.content or "")


def _stop() -> CompletionResponse:
    return CompletionResponse(
        content="Done.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-model-001",
    )


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    return ctx.with_message(ChatMessage(role=MessageRole.USER, content="Do the task."))


class _StubInbox:
    """Returns the directive once, honouring the consume-once adopted set."""

    def __init__(self, directives: tuple[ActiveSteeringDirective, ...]) -> None:
        self._directives = directives

    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        return tuple(d for d in self._directives if d.entry_id not in already_adopted)


@pytest.mark.unit
class TestReactLoopSteering:
    """The loop adopts a pending directive at the turn boundary."""

    async def test_directive_injected_before_llm_call(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop()])

        loop = ReactLoop(steering_inbox=_StubInbox((_DIRECTIVE,)))

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason is TerminationReason.COMPLETED
        # Conversation: user msg, injected steering USER msg, assistant msg.
        steering_msgs = [m for m in result.context.conversation if _is_steering_msg(m)]
        assert len(steering_msgs) == 1
        assert "dir-1" in result.context.adopted_steering_ids

    async def test_directive_not_reinjected_across_turns(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        # Two turns: the directive must be injected exactly once.
        provider = mock_provider_factory([_stop(), _stop()])
        ctx = ctx.model_copy(update={"max_turns": 2})

        loop = ReactLoop(steering_inbox=_StubInbox((_DIRECTIVE,)))
        result = await loop.execute(context=ctx, provider=provider)

        steering_msgs = [m for m in result.context.conversation if _is_steering_msg(m)]
        assert len(steering_msgs) == 1

    async def test_adopted_directive_from_resumed_context_not_reinjected(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        # Crash-resume: a context restored from a checkpoint already carries
        # the adopted directive id, so the inbox's consume-once filter must
        # not re-inject it on the first post-resume turn.
        ctx = _ctx_with_user_msg(sample_agent_context).with_steering_adopted(
            NotBlankStr("dir-1")
        )
        provider = mock_provider_factory([_stop()])

        loop = ReactLoop(steering_inbox=_StubInbox((_DIRECTIVE,)))
        result = await loop.execute(context=ctx, provider=provider)

        steering_msgs = [m for m in result.context.conversation if _is_steering_msg(m)]
        assert steering_msgs == []


@pytest.mark.unit
class TestReactLoopCancellation:
    """A cancelled task halts the loop at the next safe boundary."""

    async def test_cancelled_task_halts_before_llm(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory([_stop()])

        async def _cancelled() -> bool:
            return True

        loop = ReactLoop()
        result = await loop.execute(
            context=ctx,
            provider=provider,
            task_cancellation_checker=_cancelled,
        )

        assert result.termination_reason is TerminationReason.CANCELLED
        assert result.turns == ()
        # The halt must not drive a status re-transition: the operator's
        # CANCELLED write stands, no phantom COMPLETED/IN_REVIEW over it.
        assert result.context.task_execution is not None
        assert result.context.task_execution.status is not TaskStatus.COMPLETED
        assert result.context.task_execution.status is not TaskStatus.IN_REVIEW
