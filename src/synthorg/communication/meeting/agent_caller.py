""":data:`AgentCaller` factories for meeting orchestration.

The meeting orchestrator invokes agents through an :data:`AgentCaller`
callable with the signature ``(agent_id, prompt, max_tokens) ->
AgentResponse``.  This module provides two factories:

- :func:`build_meeting_agent_caller` -- the real caller used in
  production.  Composes an :class:`AgentRegistryService` (for agent
  identity lookup) with a :class:`ProviderRegistry` (for LLM dispatch)
  and runs one ``provider.complete()`` call per invocation.
- :func:`build_unconfigured_meeting_agent_caller` -- a fallback caller
  used when registries are not yet available at wire time.  It raises
  :class:`MeetingAgentCallerNotConfiguredError` at call time, replacing
  the old silent empty-response stub.  Operators see a loud failure
  instead of meaningless meeting contributions.

One turn = one LLM call.  Meeting protocols (round robin, position
papers, structured phases) are responsible for sequencing turns; this
module only runs a single agent's inference.
"""

from typing import TYPE_CHECKING

from synthorg.communication.meeting.models import AgentResponse
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    untrusted_content_directive,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_AGENT_CALL_FAILED,
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from synthorg.communication.meeting.protocol import AgentCaller
    from synthorg.core.agent import AgentIdentity
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class UnknownMeetingAgentError(LookupError):
    """Raised when the meeting orchestrator invokes an unregistered agent.

    Attributes:
        agent_id: The agent identifier that was not found in the registry.
    """

    def __init__(self, agent_id: NotBlankStr) -> None:
        super().__init__(
            f"Meeting agent {agent_id!r} is not registered in the "
            f"agent registry; cannot dispatch LLM call"
        )
        self.agent_id: NotBlankStr = agent_id


def build_meeting_agent_caller(
    *,
    agent_registry: AgentRegistryService,
    provider_registry: ProviderRegistry,
) -> AgentCaller:
    """Construct a meeting :data:`AgentCaller` backed by real services.

    Args:
        agent_registry: Source of truth for agent identities.
        provider_registry: Source of truth for LLM providers.

    Returns:
        An async callback matching the :data:`AgentCaller` contract.
    """

    async def _caller(
        agent_id: str,
        prompt: str,
        max_tokens: int,
    ) -> AgentResponse:
        typed_agent_id = NotBlankStr(agent_id)
        logger.info(
            MEETING_AGENT_CALLED,
            agent_id=agent_id,
            max_tokens=max_tokens,
            prompt_length=len(prompt),
        )
        identity = await agent_registry.get(typed_agent_id)
        if identity is None:
            logger.warning(
                MEETING_AGENT_CALL_FAILED,
                agent_id=agent_id,
                error_type="UnknownMeetingAgentError",
            )
            raise UnknownMeetingAgentError(typed_agent_id)

        provider_name = str(identity.model.provider)
        provider = provider_registry.get(provider_name)
        messages = _build_messages(identity, prompt)
        effective_max_tokens = min(max_tokens, identity.model.max_tokens)
        config = CompletionConfig(
            temperature=identity.model.temperature,
            max_tokens=effective_max_tokens,
        )
        try:
            response = await provider.complete(
                messages,
                str(identity.model.model_id),
                config=config,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MEETING_AGENT_CALL_FAILED,
                agent_id=agent_id,
                provider=provider_name,
                error_type=type(exc).__name__,
            )
            raise
        agent_response = AgentResponse(
            agent_id=typed_agent_id,
            content=response.content or "",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost=response.usage.cost,
        )
        logger.info(
            MEETING_AGENT_RESPONDED,
            agent_id=agent_id,
            input_tokens=agent_response.input_tokens,
            output_tokens=agent_response.output_tokens,
            cost=agent_response.cost,
        )
        return agent_response

    return _caller


def _build_messages(
    identity: AgentIdentity,
    prompt: str,
) -> list[ChatMessage]:
    """Assemble the minimal ``system`` + ``user`` pair for a meeting turn.

    The system message is derived from the agent identity (role +
    personality traits) so the LLM stays in character across the
    meeting.  Protocols inject the full turn context into ``prompt``
    (agenda, prior contributions, lens), so the system prompt only
    carries agent-stable identity.
    """
    system_content = _render_system_prompt(identity)
    return [
        ChatMessage(role=MessageRole.SYSTEM, content=system_content),
        ChatMessage(role=MessageRole.USER, content=prompt),
    ]


def _render_system_prompt(identity: AgentIdentity) -> str:
    """Render a compact system prompt from an :class:`AgentIdentity`.

    SEC-1 / #1596: appends the canonical ``untrusted_content_directive``
    listing every fence the meeting protocols may emit (``<task-data>``
    for the agenda payload, ``<peer-contribution>`` for upstream agent
    turns).  The agent_caller is the single place that builds the
    meeting agent's system prompt, so every protocol gets the directive
    for free regardless of which one builds the user message.
    """
    lines: list[str] = [
        f"You are {identity.name}, a {identity.role} "
        f"in the {identity.department} department.",
        f"Seniority level: {identity.level.value}.",
    ]
    traits = identity.personality.traits
    if traits:
        lines.append("Personality traits: " + ", ".join(traits) + ".")
    communication_style = identity.personality.communication_style
    if communication_style:
        lines.append(f"Communication style: {communication_style}.")
    body = "\n".join(lines)
    directive = untrusted_content_directive(
        (TAG_TASK_DATA, TAG_PEER_CONTRIBUTION),
    )
    return f"{body}\n\n{directive}"


class MeetingAgentCallerNotConfiguredError(RuntimeError):
    """Raised when a meeting runs without an agent + provider registry.

    The meeting orchestrator is structurally wired during Phase 1 so
    the REST surface is never 503, but calling an agent requires the
    agent registry (for identity lookup) and the provider registry
    (for LLM dispatch).  When either is absent at wire time, meetings
    that try to invoke an agent receive this error instead of the
    previous silent empty-response stub.

    Attributes:
        agent_id: The agent identifier that the meeting tried to invoke.
        missing_dependencies: Names of the dependencies that were
            absent at wire time (e.g. ``("agent_registry",
            "provider_registry")``).  Guaranteed non-empty: the error
            is only meaningful when at least one dependency is missing.

    Raises:
        ValueError: If *missing_dependencies* is empty -- the error is
            only meaningful when at least one dependency is missing.
    """

    def __init__(
        self,
        *,
        agent_id: NotBlankStr,
        missing_dependencies: tuple[str, ...],
    ) -> None:
        if not missing_dependencies:
            msg = (
                "MeetingAgentCallerNotConfiguredError requires at least one "
                "entry in missing_dependencies"
            )
            raise ValueError(msg)
        missing = ", ".join(missing_dependencies)
        super().__init__(
            f"Meeting agent caller invoked for {agent_id!r} but the "
            f"following dependencies were missing at wire time: "
            f"{missing}.  Provide them via create_app(...) so meeting "
            f"turns can dispatch real LLM calls."
        )
        self.agent_id: NotBlankStr = agent_id
        self.missing_dependencies: tuple[str, ...] = missing_dependencies


def build_unconfigured_meeting_agent_caller(
    *,
    missing_dependencies: tuple[str, ...],
) -> AgentCaller:
    """Return a caller that raises loudly if invoked.

    Used when the orchestrator is wired before the agent / provider
    registries are available.  Surfaces the root cause to operators
    at first use rather than silently succeeding with empty content.

    Args:
        missing_dependencies: Names of the dependencies missing at wire
            time.  Must be non-empty.

    Raises:
        ValueError: If *missing_dependencies* is empty.
    """
    if not missing_dependencies:
        msg = (
            "build_unconfigured_meeting_agent_caller requires at least one "
            "entry in missing_dependencies"
        )
        raise ValueError(msg)

    async def _caller(
        agent_id: str,
        _prompt: str,
        _max_tokens: int,
    ) -> AgentResponse:
        logger.warning(
            MEETING_AGENT_CALL_FAILED,
            agent_id=agent_id,
            error_type="MeetingAgentCallerNotConfiguredError",
            missing_dependencies=missing_dependencies,
        )
        raise MeetingAgentCallerNotConfiguredError(
            agent_id=NotBlankStr(agent_id),
            missing_dependencies=missing_dependencies,
        )

    return _caller


__all__ = [
    "MeetingAgentCallerNotConfiguredError",
    "UnknownMeetingAgentError",
    "build_meeting_agent_caller",
    "build_unconfigured_meeting_agent_caller",
]
