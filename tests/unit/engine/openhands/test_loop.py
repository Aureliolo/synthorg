"""Unit tests for the OpenHands execution-loop adapter."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionLoop,
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.engine.openhands.conversation import (
    EventSink,
    OpenHandsConversation,
    OpenHandsOutcome,
    OpenHandsRunSpec,
)
from synthorg.engine.openhands.errors import OpenHandsUnavailableError
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind
from synthorg.engine.openhands.loop import OpenHandsLoop
from synthorg.engine.prompt_template import TOOL_CATALOGUE_HEADING
from synthorg.llm.gateway_errors import GatewayTokenInvalidError
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.observability.events.execution import EXECUTION_MAX_TURNS_EXCEEDED
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.providers.protocol import CompletionProvider
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_SECRET = b"o" * 32
_TTL: Final[int] = 900
"""A bearer lifetime distinct from the config default, so an assertion on it
cannot pass by coincidence if the loop stopped reading the config."""


class _FakeConversation:
    """Emits scripted events to the sink, honouring an early stop."""

    def __init__(self, events: tuple[OpenHandsEvent, ...], sink: EventSink) -> None:
        self._events = events
        self._sink = sink

    async def run(self) -> OpenHandsOutcome:
        finished = True
        for event in self._events:
            if not await self._sink(event):
                finished = False
                break
        return OpenHandsOutcome(finished=finished)


def _factory(
    events: tuple[OpenHandsEvent, ...],
    captured: dict[str, object],
) -> Callable[[OpenHandsRunSpec, EventSink], Awaitable[OpenHandsConversation]]:
    async def _build(spec: OpenHandsRunSpec, sink: EventSink) -> OpenHandsConversation:
        captured["spec"] = spec
        return _FakeConversation(events, sink)

    return _build


def _deps(
    events: tuple[OpenHandsEvent, ...],
    captured: dict[str, object],
    *,
    gateway_url: str = "http://gateway",
    mcp_url: str = "http://mcp",
) -> OpenHandsLoopDeps:
    return OpenHandsLoopDeps(
        build_conversation=_factory(events, captured),
        signer=GatewaySigner(secret=_SECRET, clock=FakeClock()),
        gateway_base_url=gateway_url,
        mcp_base_url=mcp_url,
        clock=FakeClock(),
    )


def _bound(agent: AgentIdentity) -> AgentIdentity:
    model = agent.model.model_copy(
        update={"provider": "example-provider", "model_id": "example-large-001"}
    )
    return agent.model_copy(update={"model": model})


def _work_task(task: Task) -> Task:
    return task.model_copy(
        update={
            "artifacts_expected": (
                ExpectedArtifact(type=ArtifactType.CODE, path="src/x.py"),
            )
        }
    )


def _loop(deps: OpenHandsLoopDeps) -> OpenHandsLoop:
    return OpenHandsLoop(config=OpenHandsLoopConfig(), deps=deps)


def _action(tool: str, cost: float = 0.01) -> OpenHandsEvent:
    return OpenHandsEvent(
        kind=OpenHandsEventKind.ACTION,
        tool_name=tool,
        input_tokens=10,
        output_tokens=4,
        cost=cost,
    )


_OBSERVATION = OpenHandsEvent(kind=OpenHandsEventKind.OBSERVATION, text="ok")
_MESSAGE = OpenHandsEvent(
    kind=OpenHandsEventKind.MESSAGE, text="done", input_tokens=5, output_tokens=2
)
_FINISHED = OpenHandsEvent(kind=OpenHandsEventKind.FINISHED)


async def _run(
    agent: AgentIdentity, task: Task, deps: OpenHandsLoopDeps
) -> ExecutionResult:
    ctx = AgentContext.from_identity(_bound(agent), task=task)
    provider = mock_of[CompletionProvider]()
    return await _loop(deps).execute(context=ctx, provider=provider)


def test_loop_satisfies_the_execution_loop_protocol() -> None:
    loop = _loop(_deps((), {}))

    assert isinstance(loop, ExecutionLoop)
    assert loop.get_loop_type() == "openhands"


def test_registry_builds_openhands_with_deps() -> None:
    from synthorg.engine.loop_selector import (
        build_execution_loop,
        registered_loop_types,
    )

    assert "openhands" in registered_loop_types()
    loop = build_execution_loop("openhands", openhands_loop_deps=_deps((), {}))
    assert loop.get_loop_type() == "openhands"


def test_registry_openhands_without_deps_fails_loud() -> None:
    from synthorg.engine.loop_selector import build_execution_loop

    with pytest.raises(OpenHandsUnavailableError):
        build_execution_loop("openhands")


async def test_completed_run_maps_events_to_turns(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    events = (_action("edit_file"), _OBSERVATION, _MESSAGE, _FINISHED)
    result = await _run(
        sample_agent_with_personality, sample_task_with_criteria, _deps(events, {})
    )

    assert result.termination_reason is TerminationReason.COMPLETED
    assert len(result.turns) == 2
    assert result.total_tool_calls == 1


async def test_zero_tool_work_run_is_no_op(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    events = (_MESSAGE, _FINISHED)
    result = await _run(
        sample_agent_with_personality,
        _work_task(sample_task_with_criteria),
        _deps(events, {}),
    )

    assert result.termination_reason is TerminationReason.NO_OP
    assert result.error_message is not None
    assert "no artifacts" in result.error_message


async def test_error_event_terminates_error(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    events = (OpenHandsEvent(kind=OpenHandsEventKind.ERROR, text="boom"),)
    result = await _run(
        sample_agent_with_personality, sample_task_with_criteria, _deps(events, {})
    )

    assert result.termination_reason is TerminationReason.ERROR
    assert result.error_message is not None
    assert "boom" in result.error_message


async def test_a_rejected_tool_call_costs_a_turn_not_the_run(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    """The harness rejects a bad call so the model can fix it, and keeps going.

    An unknown tool name or an argument that does not validate reaches the
    model as an observation, and the next turn corrects it. Ending the run
    instead spends a whole repetition on a typo, and the native loop returns
    the same class of error to its own model and carries on: a scoreboard
    comparing the two would be reading one loop's recovery against the other's
    execution.
    """
    events = (
        _action("shel"),
        OpenHandsEvent(
            kind=OpenHandsEventKind.TOOL_ERROR,
            text="Tool 'shel' not found. Available: ['terminal', 'file_editor']",
            tool_name="shel",
        ),
        _action("terminal"),
        _FINISHED,
    )
    result = await _run(
        sample_agent_with_personality, sample_task_with_criteria, _deps(events, {})
    )

    assert result.termination_reason is TerminationReason.COMPLETED
    assert [turn.tool_calls_made for turn in result.turns] == [
        ("shel",),
        ("terminal",),
    ]


async def test_a_fatal_container_error_still_terminates(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    """The kind that means the run itself failed keeps ending the run."""
    events = (
        _action("terminal"),
        OpenHandsEvent(kind=OpenHandsEventKind.ERROR, text="empty run spec on stdin"),
    )
    result = await _run(
        sample_agent_with_personality, sample_task_with_criteria, _deps(events, {})
    )

    assert result.termination_reason is TerminationReason.ERROR


async def test_budget_exhaustion_stops_at_event_boundary(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    events = (_action("a"), _action("b"), _FINISHED)
    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality), task=sample_task_with_criteria
    )
    result = await _loop(_deps(events, {})).execute(
        context=ctx,
        provider=mock_of[CompletionProvider](),
        budget_checker=lambda _ctx: True,
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(result.turns) == 1


async def test_the_harness_is_given_the_agent_s_own_system_prompt(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    """The loop must not throw away what the engine already built for it.

    The engine builds the full system prompt (identity, house style, skills,
    authority, autonomy, ask policy, untrusted-content discipline) and puts it
    at the head of the context. Sending only the task title and description
    hands this loop a materially smaller brief than the native one gets for the
    same task, which is a difference between the integrations rather than
    between the loops.
    """
    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality), task=sample_task_with_criteria
    )
    system = "You are a careful engineer. House style: no em-dashes."
    ctx = ctx.model_copy(
        update={
            "conversation": (
                ChatMessage(role=MessageRole.SYSTEM, content=system),
                *ctx.conversation,
            )
        }
    )
    captured: dict[str, object] = {}

    await _loop(_deps((_FINISHED,), captured)).execute(
        context=ctx, provider=mock_of[CompletionProvider]()
    )

    spec = captured["spec"]
    assert isinstance(spec, OpenHandsRunSpec)
    assert spec.system_prompt == system


async def test_the_native_tool_catalogue_does_not_reach_the_harness(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    """The one part of the prompt that describes the other loop's tools.

    The catalogue asserts that a tool it does not list does not exist in the
    session. Forwarded into a harness holding its own tools, it names tools
    this run cannot reach, and the model calls one: the run then dies on an
    unknown-tool error having produced nothing, which reads on a scoreboard as
    the loop failing the brief.
    """
    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality), task=sample_task_with_criteria
    )
    system = (
        "## Identity\n\nYou are a careful engineer.\n\n"
        f"{TOOL_CATALOGUE_HEADING}\n\n"
        "You can call these 2 tools directly. A tool not listed here does not "
        "exist in this session.\n\n"
        "- **read_file** (file_system, medium): Read a file.\n"
        "- **shell_command** (terminal, medium): Run a command.\n\n"
        "## Authority\n\n- **Reports to**: nobody\n"
    )
    ctx = ctx.model_copy(
        update={
            "conversation": (
                ChatMessage(role=MessageRole.SYSTEM, content=system),
                *ctx.conversation,
            )
        }
    )
    captured: dict[str, object] = {}

    await _loop(_deps((_FINISHED,), captured)).execute(
        context=ctx, provider=mock_of[CompletionProvider]()
    )

    spec = captured["spec"]
    assert isinstance(spec, OpenHandsRunSpec)
    forwarded = str(spec.system_prompt)
    assert "You are a careful engineer." in forwarded
    assert "Reports to" in forwarded
    assert "read_file" not in forwarded
    assert TOOL_CATALOGUE_HEADING not in forwarded


async def test_the_turn_ceiling_names_itself(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    """A ceiling hit emits the fact the scorers key on.

    This loop reaches its ceiling by its own route rather than through
    ``ceiling_result``, so the event the native loop emits there does not cover
    it. A recorded matrix showed five ceiling terminations from this loop and
    reported no governance event for any of them.
    """
    events = (_action("a"), _action("b"), _action("c"), _FINISHED)
    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality),
        task=sample_task_with_criteria,
        max_turns=2,
    )

    with structlog.testing.capture_logs() as logs:
        result = await _loop(_deps(events, {})).execute(
            context=ctx, provider=mock_of[CompletionProvider]()
        )

    assert result.termination_reason is TerminationReason.MAX_TURNS
    assert EXECUTION_MAX_TURNS_EXCEEDED in [entry["event"] for entry in logs]


async def test_shutdown_stops_at_event_boundary(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    events = (_action("a"), _FINISHED)
    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality), task=sample_task_with_criteria
    )
    result = await _loop(_deps(events, {})).execute(
        context=ctx,
        provider=mock_of[CompletionProvider](),
        shutdown_checker=lambda: True,
    )

    assert result.termination_reason is TerminationReason.SHUTDOWN


async def test_cancellation_stops_at_event_boundary(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    events = (_action("a"), _FINISHED)

    async def _cancelled() -> bool:
        return True

    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality), task=sample_task_with_criteria
    )
    result = await _loop(_deps(events, {})).execute(
        context=ctx,
        provider=mock_of[CompletionProvider](),
        task_cancellation_checker=_cancelled,
    )

    assert result.termination_reason is TerminationReason.CANCELLED


def test_unconfigured_endpoints_fail_loud() -> None:
    # Unwired boundaries fail loud at deps construction (the wiring returns
    # None rather than constructing with blank URLs), not at execute time.
    with pytest.raises(ValueError, match="gateway_base_url must be non-blank"):
        _deps((), {}, gateway_url="")
    with pytest.raises(ValueError, match="mcp_base_url must be non-blank"):
        _deps((), {}, mcp_url="")


async def test_spec_binds_gateway_token_and_urls(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    captured: dict[str, object] = {}
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())
    deps = OpenHandsLoopDeps(
        build_conversation=_factory((_FINISHED,), captured),
        signer=signer,
        gateway_base_url="http://gateway",
        mcp_base_url="http://mcp",
        clock=FakeClock(),
    )
    task = sample_task_with_criteria
    ctx = AgentContext.from_identity(_bound(sample_agent_with_personality), task=task)
    await _loop(deps).execute(context=ctx, provider=mock_of[CompletionProvider]())

    spec = captured["spec"]
    assert isinstance(spec, OpenHandsRunSpec)
    assert spec.gateway_base_url == "http://gateway"
    assert spec.mcp_base_url == "http://mcp"
    assert spec.model == "example-large-001"
    assert spec.conversation_id == task.id
    # The minted bearer verifies under the same signer and carries the
    # explicitly-bound (provider, model) pair.
    claims = signer.verify(spec.gateway_token)
    assert claims.provider == "example-provider"
    assert claims.model_id == "example-large-001"


async def test_bearer_lifetime_comes_from_the_frozen_config(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> None:
    # ``providers.gateway_token_ttl_seconds`` is resolved once, while the
    # runtime is assembled, and frozen onto OpenHandsLoopConfig; the loop mints
    # from that stored value rather than re-reading the setting per run. That is
    # what makes the key construction-only, so a re-read here would silently
    # promote it to live and falsify its baseline entry.
    minted_at = datetime(2026, 1, 1, tzinfo=UTC)
    captured: dict[str, object] = {}
    deps = OpenHandsLoopDeps(
        build_conversation=_factory((_FINISHED,), captured),
        signer=GatewaySigner(secret=_SECRET, clock=FakeClock(start=minted_at)),
        gateway_base_url="http://gateway",
        mcp_base_url="http://mcp",
        clock=FakeClock(start=minted_at),
    )
    ctx = AgentContext.from_identity(
        _bound(sample_agent_with_personality), task=sample_task_with_criteria
    )
    loop = OpenHandsLoop(config=OpenHandsLoopConfig(token_ttl_seconds=_TTL), deps=deps)
    await loop.execute(context=ctx, provider=mock_of[CompletionProvider]())

    spec = captured["spec"]
    assert isinstance(spec, OpenHandsRunSpec)
    # Bracket the configured lifetime: a verifier one second inside it accepts
    # the bearer, one second past rejects it. Asserting the boundary rather than
    # a decoded field keeps the check on observable behaviour.
    inside = GatewaySigner(
        secret=_SECRET,
        clock=FakeClock(start=minted_at + timedelta(seconds=_TTL - 1)),
    )
    assert inside.verify(spec.gateway_token).model_id == "example-large-001"
    expired = GatewaySigner(
        secret=_SECRET,
        clock=FakeClock(start=minted_at + timedelta(seconds=_TTL + 1)),
    )
    with pytest.raises(GatewayTokenInvalidError):
        expired.verify(spec.gateway_token)
