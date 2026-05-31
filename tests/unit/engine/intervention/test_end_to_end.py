"""End-to-end steering: issue -> brain -> inbox -> in-flight loop adoption.

Drives a real :class:`ReactLoop` reading active directives from the real
:class:`BrainBackedSteeringInbox`, fed by the brain repository the real
:class:`SteeringService` wrote to. The operator issues a redirect (with an
explicit supersede) before the agent's turn; the agent then adopts it at its
turn boundary, the obsolete task is cancelled through the task engine, and the
brain records the directive. This is the runnable unit-level proxy for the
#1997 acceptance criterion; ``tests/integration/intervention`` drives the same
mechanism under the simulation harness.
"""

import pytest

from synthorg.core.enums import InterventionKind, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention import (
    NoOpSupersessionProposer,
    SteeringService,
    build_steering_inbox,
)
from synthorg.engine.intervention.models import SupersedeMode
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.react_loop import ReactLoop
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse, TokenUsage
from tests._shared.steering import FakeBrainService
from tests.unit.api.fakes import FakeProjectBrainRepository
from tests.unit.engine.conftest import MockCompletionProvider

_PROJECT = NotBlankStr("proj-001")
_DIRECTIVE_TEXT = "use Postgres not Mongo"
_OBSOLETE_TASK = NotBlankStr("task-obsolete")


class _RecordingTaskEngine:
    """Records cancellations driven by the explicit supersede."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_task(
        self, task_id: str, *, requested_by: str, reason: str
    ) -> tuple[None, None]:
        self.cancelled.append(task_id)
        return (None, None)

    async def list_tasks(
        self, *, status: TaskStatus, project: str, limit: int
    ) -> tuple[tuple[object, ...], int]:
        return ((), 0)


def _stop() -> CompletionResponse:
    return CompletionResponse(
        content="Done.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-model-001",
    )


def _is_steering_msg(msg: ChatMessage) -> bool:
    return msg.role is MessageRole.USER and _DIRECTIVE_TEXT in (msg.content or "")


@pytest.mark.unit
class TestSteeringEndToEnd:
    async def test_redirect_propagates_brain_to_loop_and_supersedes(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        repo = FakeProjectBrainRepository()
        engine = _RecordingTaskEngine()
        service = SteeringService(
            brain_service=FakeBrainService(repo),  # type: ignore[arg-type]
            brain_repo=repo,
            task_engine=engine,  # type: ignore[arg-type]
            proposer=NoOpSupersessionProposer(),
        )
        inbox = build_steering_inbox(repo)

        # 1. The operator issues a redirect mid-flight and supersedes the
        #    now-obsolete task. The brain records it; the task is cancelled.
        result = await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.REDIRECT,
            text=NotBlankStr(_DIRECTIVE_TEXT),
            author=NotBlankStr("mission-control"),
            supersede_task_ids=(_OBSOLETE_TASK,),
            supersede_mode=SupersedeMode.EXPLICIT,
        )
        assert engine.cancelled == [_OBSOLETE_TASK]
        active = await service.list_active(project_id=_PROJECT)
        assert len(active) == 1
        assert active[0].entry_id == result.directive_id

        # 2. The in-flight agent adopts the directive at its turn boundary.
        ctx = sample_agent_context.with_message(
            ChatMessage(role=MessageRole.USER, content="Do the task.")
        )
        provider = mock_provider_factory([_stop()])
        loop = ReactLoop(steering_inbox=inbox)
        run = await loop.execute(context=ctx, provider=provider)

        assert run.termination_reason is TerminationReason.COMPLETED
        steering_msgs = [m for m in run.context.conversation if _is_steering_msg(m)]
        assert len(steering_msgs) == 1
        assert result.directive_id in run.context.adopted_steering_ids
        # A REDIRECT records a pending replan for the step-boundary consumers.
        assert run.context.pending_steering_replan_id == result.directive_id
