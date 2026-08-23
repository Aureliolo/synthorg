"""Unit tests for the multi-agent conversation caller factory."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog

from synthorg.communication.multi_agent import (
    AgentCaller,
    AgentResponse,
    UnknownConversationAgentError,
    build_agent_caller,
)
from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    untrusted_content_directive,
)
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.multi_agent import (
    MULTI_AGENT_CALL_FAILED,
    MULTI_AGENT_CALLED,
    MULTI_AGENT_RESPONDED,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.registry import ProviderRegistry

pytestmark = pytest.mark.unit

_AGENT_ID = "agent-sarah"
_CONVERSATION_ID = "conversation-test-001"


def _identity(
    *,
    name: str = "Sarah Chen",
    role: str = "engineer",
    department: str = "engineering",
    provider: str = "example-provider",
    model_id: str = "example-capable-001",
    max_tokens: int | None = 4096,
    personality: PersonalityConfig | None = None,
) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr(department),
        personality=personality
        or PersonalityConfig(
            traits=(NotBlankStr("analytical"), NotBlankStr("curious")),
            communication_style=NotBlankStr("concise"),
        ),
        model=ModelConfig(
            provider=NotBlankStr(provider),
            model_id=NotBlankStr(model_id),
            temperature=0.7,
            max_tokens=max_tokens,
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
        model=NotBlankStr("example-capable-001"),
    )


def _build_caller(
    *,
    identity: AgentIdentity | None = None,
    response: CompletionResponse | None = None,
    provider_error: Exception | None = None,
) -> tuple[AgentCaller, MagicMock]:
    """Produce ``(caller, provider_registry)``.

    Uses ``spec=`` so interface drift between the doubles and the real
    services surfaces as a test failure instead of silently passing.

    Returns:
        The built caller and the provider registry it dispatches
        through, for assertions on the outbound call.
    """
    agent_registry = MagicMock(spec=AgentRegistryService)
    agent_registry.get = AsyncMock(return_value=identity)

    provider = MagicMock(spec=BaseCompletionProvider)
    if provider_error is not None:
        provider.complete = AsyncMock(side_effect=provider_error)
    else:
        provider.complete = AsyncMock(return_value=response or _completion())

    provider_registry = MagicMock(spec=ProviderRegistry)
    provider_registry.get = MagicMock(return_value=provider)

    caller = build_agent_caller(
        agent_registry=agent_registry,
        provider_registry=provider_registry,
    )
    return caller, provider_registry


class TestBuildAgentCaller:
    async def test_round_trip_maps_completion_to_agent_response(self) -> None:
        caller, _providers = _build_caller(
            identity=_identity(),
            response=_completion(
                content="I propose adding a queue.",
                input_tokens=20,
                output_tokens=30,
                cost=0.001,
            ),
        )

        result = await caller(_AGENT_ID, "Agenda: queueing", 500, _CONVERSATION_ID)

        assert isinstance(result, AgentResponse)
        assert result.agent_id == _AGENT_ID
        assert result.content == "I propose adding a queue."
        assert result.input_tokens == 20
        assert result.output_tokens == 30
        assert result.cost == pytest.approx(0.001)

    async def test_unknown_agent_raises(self) -> None:
        caller, _providers = _build_caller(identity=None)

        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(UnknownConversationAgentError) as exc_info,
        ):
            await caller(_AGENT_ID, "prompt", 100, _CONVERSATION_ID)

        # NotFoundError-rooted so the API layer's RFC 9457 dispatch
        # produces a structured 404 instead of an opaque 500.
        assert isinstance(exc_info.value, NotFoundError)
        assert exc_info.value.agent_id == _AGENT_ID
        # Error path must log before raising so operators see agent_id
        # in structured logs even when the error is caught upstream.
        failures = [e for e in cap if e.get("event") == MULTI_AGENT_CALL_FAILED]
        assert len(failures) == 1
        assert failures[0]["agent_id"] == _AGENT_ID
        assert failures[0]["error_type"] == "UnknownConversationAgentError"

    async def test_empty_content_maps_to_empty_string(self) -> None:
        caller, _providers = _build_caller(
            identity=_identity(),
            response=_completion(content=""),
        )
        result = await caller(_AGENT_ID, "prompt", 100, _CONVERSATION_ID)
        assert result.content == ""

    async def test_blank_conversation_id_rejected(self) -> None:
        """Cost attribution needs a real conversation id, not whitespace."""
        caller, _providers = _build_caller(identity=_identity())
        with pytest.raises(ValueError, match="conversation_id"):
            await caller(_AGENT_ID, "prompt", 100, "   ")

    async def test_provider_error_propagates(self) -> None:
        caller, _providers = _build_caller(
            identity=_identity(),
            provider_error=RuntimeError("provider boom"),
        )
        with pytest.raises(RuntimeError, match="provider boom"):
            await caller(_AGENT_ID, "prompt", 100, _CONVERSATION_ID)

    async def test_provider_error_logs_failure_event_before_raising(self) -> None:
        caller, _providers = _build_caller(
            identity=_identity(),
            provider_error=RuntimeError("provider boom"),
        )

        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(RuntimeError, match="provider boom"),
        ):
            await caller(_AGENT_ID, "prompt", 100, _CONVERSATION_ID)

        failures = [e for e in cap if e.get("event") == MULTI_AGENT_CALL_FAILED]
        assert len(failures) == 1
        assert failures[0]["agent_id"] == _AGENT_ID
        assert failures[0]["error_type"] == "RuntimeError"
        assert "provider boom" in failures[0]["error"]

    async def test_logs_called_and_responded_events(self) -> None:
        caller, _providers = _build_caller(identity=_identity())

        with structlog.testing.capture_logs() as cap:
            await caller(_AGENT_ID, "prompt", 100, _CONVERSATION_ID)

        events = [e.get("event") for e in cap]
        assert MULTI_AGENT_CALLED in events
        assert MULTI_AGENT_RESPONDED in events

    async def test_dispatches_to_the_agents_own_provider(self) -> None:
        caller, provider_registry = _build_caller(
            identity=_identity(provider="example-provider"),
        )
        await caller(_AGENT_ID, "prompt", 256, _CONVERSATION_ID)
        provider_registry.get.assert_called_once_with("example-provider")

    async def test_passes_max_tokens_into_completion_config(self) -> None:
        caller, provider_registry = _build_caller(identity=_identity())
        await caller(_AGENT_ID, "agenda", 777, _CONVERSATION_ID)

        provider = provider_registry.get.return_value
        provider.complete.assert_awaited_once()
        call = provider.complete.await_args
        messages = call.args[0]
        assert call.kwargs["config"].max_tokens == 777
        assert messages[0].role == MessageRole.SYSTEM
        assert messages[1].role == MessageRole.USER
        assert "agenda" in (messages[1].content or "")

    async def test_clamps_max_tokens_to_identity_cap(self) -> None:
        """A caller asking for more than the model allows is clamped down."""
        caller, provider_registry = _build_caller(identity=_identity())
        await caller(_AGENT_ID, "agenda", 10_000, _CONVERSATION_ID)

        provider = provider_registry.get.return_value
        # Without the clamp the per-turn request would overshoot the
        # agent's configured limit and the provider would either reject
        # or silently truncate.
        assert provider.complete.await_args.kwargs["config"].max_tokens == 4096

    async def test_an_unbound_agent_takes_the_conversations_own_cap(self) -> None:
        """The default binding sets no ceiling, so this is the common case.

        The clamp is a MINIMUM of two numbers and an agent that states
        none has nothing to contribute to it: without the ``None``
        branch the conversation would compare its cap against an
        absence.
        """
        caller, provider_registry = _build_caller(
            identity=_identity(max_tokens=None),
        )
        await caller(_AGENT_ID, "agenda", 10_000, _CONVERSATION_ID)

        provider = provider_registry.get.return_value
        assert provider.complete.await_args.kwargs["config"].max_tokens == 10_000

    async def test_renders_prompt_without_traits_when_tuple_empty(self) -> None:
        """Empty traits render without a Personality traits line.

        ``PersonalityConfig.traits`` defaults to an empty tuple and
        ``communication_style`` defaults to ``"neutral"``; both are
        conditionally rendered.
        """
        caller, provider_registry = _build_caller(
            identity=_identity(personality=PersonalityConfig()),
        )
        await caller(_AGENT_ID, "agenda", 100, _CONVERSATION_ID)

        messages = provider_registry.get.return_value.complete.await_args.args[0]
        system_content = messages[0].content or ""
        assert "Personality traits" not in system_content
        assert "Communication style: neutral." in system_content

    async def test_system_prompt_carries_untrusted_directive(self) -> None:
        """Every conversation LLM call carries the untrusted-content directive.

        The caller is the single place that renders the participant's
        system prompt, so fencing it here covers every conversation
        shape regardless of which one built the user message.
        """
        caller, provider_registry = _build_caller(identity=_identity())
        await caller(_AGENT_ID, "agenda", 100, _CONVERSATION_ID)

        messages = provider_registry.get.return_value.complete.await_args.args[0]
        expected = untrusted_content_directive(
            (TAG_TASK_DATA, TAG_PEER_CONTRIBUTION),
        )
        assert expected in (messages[0].content or "")
