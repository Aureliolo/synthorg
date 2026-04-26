"""Unit tests for the meeting agent caller factory."""

from datetime import UTC, date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog

from synthorg.communication.meeting.agent_caller import (
    MeetingAgentCallerNotConfiguredError,
    UnknownMeetingAgentError,
    build_meeting_agent_caller,
    build_unconfigured_meeting_agent_caller,
)
from synthorg.communication.meeting.models import AgentResponse
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
)
from synthorg.core.enums import AgentStatus, SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.meeting import (
    MEETING_AGENT_CALL_FAILED,
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.registry import ProviderRegistry

pytestmark = pytest.mark.unit


def _identity(
    *,
    name: str = "Sarah Chen",
    role: str = "engineer",
    department: str = "engineering",
    provider: str = "example-provider",
    model_id: str = "example-medium-001",
) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr(department),
        level=SeniorityLevel.MID,
        personality=PersonalityConfig(
            traits=(NotBlankStr("analytical"), NotBlankStr("curious")),
            communication_style=NotBlankStr("concise"),
        ),
        model=ModelConfig(
            provider=NotBlankStr(provider),
            model_id=NotBlankStr(model_id),
            temperature=0.7,
            max_tokens=4096,
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _completion(
    *,
    content: str = "Here is my input.",
    input_tokens: int = 17,
    output_tokens: int = 42,
    cost: float = 0.00042,
) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        ),
        model=NotBlankStr("example-medium-001"),
    )


_AGENT_ID = "agent-sarah"


def _build_caller(
    *,
    identity: AgentIdentity | None = None,
    response: CompletionResponse | None = None,
    provider_error: Exception | None = None,
) -> tuple[AgentCaller, MagicMock, MagicMock]:
    """Produce ``(caller, agent_registry, provider_registry)``.

    Uses ``spec=`` so interface drift between the mocks and the real
    services surfaces as a test failure instead of silently passing.
    """
    agent_registry = MagicMock(spec=AgentRegistryService)
    agent_registry.get = AsyncMock(return_value=identity)

    provider = MagicMock()
    if provider_error is not None:
        provider.complete = AsyncMock(side_effect=provider_error)
    else:
        provider.complete = AsyncMock(
            return_value=response or _completion(),
        )

    provider_registry = MagicMock(spec=ProviderRegistry)
    provider_registry.get = MagicMock(return_value=provider)

    caller = build_meeting_agent_caller(
        agent_registry=agent_registry,
        provider_registry=provider_registry,
    )
    return caller, agent_registry, provider_registry


class TestBuildMeetingAgentCaller:
    async def test_round_trip_maps_completion_to_agent_response(self) -> None:
        identity = _identity()
        response = _completion(
            content="I propose adding a queue.",
            input_tokens=20,
            output_tokens=30,
            cost=0.001,
        )
        caller, _registry, _providers = _build_caller(
            identity=identity,
            response=response,
        )

        result = await caller(_AGENT_ID, "Agenda: queueing", 500)
        assert isinstance(result, AgentResponse)
        assert result.agent_id == _AGENT_ID
        assert result.content == "I propose adding a queue."
        assert result.input_tokens == 20
        assert result.output_tokens == 30
        assert result.cost == pytest.approx(0.001)

    async def test_unknown_agent_raises(self) -> None:
        caller, _reg, _providers = _build_caller(identity=None)
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(UnknownMeetingAgentError) as exc_info,
        ):
            await caller(_AGENT_ID, "prompt", 100)
        # LookupError-compatible so callers can catch with existing
        # lookup-failure handlers.
        assert isinstance(exc_info.value, LookupError)
        # agent_id must be available as a typed attribute for
        # programmatic handling (logging, retries, metric tagging).
        assert exc_info.value.agent_id == _AGENT_ID
        # Error path must log before raising so operators see agent_id
        # in structured logs even when the error is caught upstream.
        failures = [e for e in cap if e.get("event") == MEETING_AGENT_CALL_FAILED]
        assert len(failures) == 1
        assert failures[0]["agent_id"] == _AGENT_ID
        assert failures[0]["error_type"] == "UnknownMeetingAgentError"

    async def test_empty_content_maps_to_empty_string(self) -> None:
        identity = _identity()
        caller, _reg, _providers = _build_caller(
            identity=identity,
            response=_completion(content=""),
        )
        result = await caller(_AGENT_ID, "prompt", 100)
        assert result.content == ""

    async def test_provider_error_propagates(self) -> None:
        identity = _identity()
        caller, _reg, _providers = _build_caller(
            identity=identity,
            provider_error=RuntimeError("provider boom"),
        )
        with pytest.raises(RuntimeError, match="provider boom"):
            await caller(_AGENT_ID, "prompt", 100)

    async def test_logs_called_and_responded_events(self) -> None:
        identity = _identity()
        caller, _reg, _providers = _build_caller(identity=identity)

        with structlog.testing.capture_logs() as cap:
            await caller(_AGENT_ID, "prompt", 100)
        events = [e.get("event") for e in cap]
        assert MEETING_AGENT_CALLED in events
        assert MEETING_AGENT_RESPONDED in events

    async def test_dispatches_to_agent_provider(self) -> None:
        identity = _identity(provider="example-provider")
        caller, _reg, provider_registry = _build_caller(identity=identity)
        await caller(_AGENT_ID, "prompt", 256)
        provider_registry.get.assert_called_once_with("example-provider")

    async def test_passes_max_tokens_into_completion_config(self) -> None:
        identity = _identity()
        caller, _reg, provider_registry = _build_caller(identity=identity)
        await caller(_AGENT_ID, "agenda", 777)
        provider = provider_registry.get.return_value
        provider.complete.assert_awaited_once()
        call = provider.complete.await_args
        messages = call.args[0]
        config = call.kwargs["config"]
        assert config.max_tokens == 777
        assert messages[0].role == MessageRole.SYSTEM
        assert messages[1].role == MessageRole.USER
        assert "agenda" in (messages[1].content or "")

    async def test_clamps_max_tokens_to_identity_cap(self) -> None:
        """A caller asking for more than the model allows is clamped down."""
        identity = _identity()
        assert identity.model.max_tokens == 4096
        caller, _reg, provider_registry = _build_caller(identity=identity)
        await caller(_AGENT_ID, "agenda", 10_000)
        provider = provider_registry.get.return_value
        config = provider.complete.await_args.kwargs["config"]
        # min(10_000, 4096) == 4096.  Without the clamp the per-turn
        # request would overshoot the agent's configured limit and the
        # provider would either reject or silently truncate.
        assert config.max_tokens == 4096

    async def test_provider_error_logs_failure_event_before_raising(self) -> None:
        identity = _identity()
        caller, _reg, _providers = _build_caller(
            identity=identity,
            provider_error=RuntimeError("provider boom"),
        )

        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(RuntimeError, match="provider boom"),
        ):
            await caller(_AGENT_ID, "prompt", 100)

        failures = [e for e in cap if e.get("event") == MEETING_AGENT_CALL_FAILED]
        assert len(failures) == 1
        assert failures[0]["agent_id"] == _AGENT_ID
        assert failures[0]["error_type"] == "RuntimeError"

    async def test_renders_prompt_without_traits_when_tuple_empty(
        self,
    ) -> None:
        """Empty traits render without a Personality traits line.

        ``PersonalityConfig.traits`` defaults to an empty tuple and
        ``communication_style`` defaults to ``"neutral"``; both are
        conditionally rendered.  This test pins the empty-traits branch
        so a regression (always rendering "Personality traits:") would
        fail fast.
        """
        identity = _identity()
        identity = AgentIdentity(
            id=identity.id,
            name=identity.name,
            role=identity.role,
            department=identity.department,
            level=identity.level,
            personality=PersonalityConfig(),
            model=identity.model,
            hiring_date=identity.hiring_date,
            status=identity.status,
        )
        caller, _reg, provider_registry = _build_caller(identity=identity)
        await caller(_AGENT_ID, "agenda", 100)
        messages = provider_registry.get.return_value.complete.await_args.args[0]
        system_content = messages[0].content or ""
        assert "Personality traits" not in system_content
        # communication_style defaults to "neutral" (NotBlankStr), which
        # is always rendered -- pin that branch too.
        assert "Communication style: neutral." in system_content

    async def test_system_prompt_carries_untrusted_directive(self) -> None:
        """SEC-1 / #1596: every meeting LLM call carries the directive.

        The agent_caller is the single place that renders the meeting
        agent's system prompt.  Appending the canonical
        ``untrusted_content_directive`` here means every protocol
        (round_robin, structured_phases, position_papers) gets the
        directive for free, regardless of which one built the user
        message.
        """
        from synthorg.engine.prompt_safety import (
            TAG_PEER_CONTRIBUTION,
            TAG_TASK_DATA,
            untrusted_content_directive,
        )

        caller, _reg, provider_registry = _build_caller(
            identity=_identity(),
        )
        await caller(_AGENT_ID, "agenda", 100)
        messages = provider_registry.get.return_value.complete.await_args.args[0]
        system_content = messages[0].content or ""
        expected = untrusted_content_directive(
            (TAG_TASK_DATA, TAG_PEER_CONTRIBUTION),
        )
        assert expected in system_content


class TestBuildUnconfiguredMeetingAgentCaller:
    async def test_caller_raises_with_typed_attributes(self) -> None:
        caller = build_unconfigured_meeting_agent_caller(
            missing_dependencies=("agent_registry", "provider_registry"),
        )
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(MeetingAgentCallerNotConfiguredError) as exc_info,
        ):
            await caller("agent-1", "prompt", 100)
        assert exc_info.value.agent_id == "agent-1"
        assert exc_info.value.missing_dependencies == (
            "agent_registry",
            "provider_registry",
        )
        # Error path must log before raising so the structured context
        # (agent_id + missing_dependencies) reaches observability even
        # when upstream callers swallow or rewrap the exception.
        failures = [e for e in cap if e.get("event") == MEETING_AGENT_CALL_FAILED]
        assert len(failures) == 1
        assert failures[0]["agent_id"] == "agent-1"
        assert failures[0]["error_type"] == "MeetingAgentCallerNotConfiguredError"
        assert failures[0]["missing_dependencies"] == (
            "agent_registry",
            "provider_registry",
        )

    def test_rejects_empty_missing_dependencies(self) -> None:
        """A caller with no missing deps is a programming error, not a wire gap."""
        with pytest.raises(ValueError, match="missing_dependencies"):
            build_unconfigured_meeting_agent_caller(missing_dependencies=())


_ = UTC  # keep datetime-aware reference for future date-sensitive tests
