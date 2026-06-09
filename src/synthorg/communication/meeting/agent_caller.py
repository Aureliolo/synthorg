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

from typing import ClassVar

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker``, ``AgentRegistryService``, ``ProviderRegistry`` and
# ``AgentCaller`` are part of the public ``build_meeting_agent_caller``
# signature so they must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).  Importing at
# module top -- not under ``TYPE_CHECKING`` -- keeps the names in
# module globals.
from synthorg.budget.tracker import CostTracker
from synthorg.communication.meeting.models import AgentResponse
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError, NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_AGENT_CALL_FAILED,
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class UnknownMeetingAgentError(NotFoundError):
    """Raised when the meeting orchestrator invokes an unregistered agent.

    Attributes:
        agent_id: The agent identifier that was not found in the registry.
    """

    default_message: ClassVar[str] = "Meeting agent not registered"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404

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
    cost_tracker: CostTracker | None = None,
) -> AgentCaller:
    """Construct a meeting :data:`AgentCaller` backed by real services.

    Args:
        agent_registry: Source of truth for agent identities.
        provider_registry: Source of truth for LLM providers.
        cost_tracker: Optional cost tracker; when wired each meeting
            turn records via the chokepoint.

    Returns:
        An async callback matching the :data:`AgentCaller` contract.
    """

    async def _caller(
        agent_id: str,
        prompt: str,
        max_tokens: int,
        meeting_id: str,
    ) -> AgentResponse:
        """Invoke the agent's provider for one meeting turn.

        Returns:
            The agent's response with token usage and cost.

        Raises:
            UnknownMeetingAgentError: If the agent id is not registered.
        """
        typed_agent_id = NotBlankStr(agent_id)
        # Validate meeting_id at the call boundary so a blank /
        # whitespace-only id surfaces as a clean ``ValueError`` here
        # rather than as a generic NotBlankStr failure inside
        # ``cost_recording_scope`` (where the prefixed
        # ``f"meeting:{meeting_id}"`` would mask the real cause).
        cleaned_meeting_id = NotBlankStr(meeting_id.strip())
        logger.info(
            MEETING_AGENT_CALLED,
            agent_id=agent_id,
            meeting_id=cleaned_meeting_id,
            max_tokens=max_tokens,
            prompt_length=len(prompt),
        )
        identity = await agent_registry.get(typed_agent_id)
        if identity is None:
            logger.warning(
                MEETING_AGENT_CALL_FAILED,
                agent_id=agent_id,
                meeting_id=cleaned_meeting_id,
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
            async with cost_recording_scope(
                cost_tracker=cost_tracker,
                agent_id=typed_agent_id,
                task_id=NotBlankStr(f"meeting:{cleaned_meeting_id}"),
                call_category=LLMCallCategory.COORDINATION,
            ):
                response = await provider.complete(
                    messages,
                    str(identity.model.model_id),
                    config=config,
                )
        except Exception as exc:
            reraise_critical(exc)
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

    Returns:
        The ``system`` + ``user`` message pair for the turn.
    """
    system_content = _render_system_prompt(identity)
    return [
        ChatMessage(role=MessageRole.SYSTEM, content=system_content),
        ChatMessage(role=MessageRole.USER, content=prompt),
    ]


def _render_system_prompt(identity: AgentIdentity) -> str:
    """Render a compact system prompt from an :class:`AgentIdentity`.

    Thin wrapper over the shared
    :func:`synthorg.engine.agent_persona.render_agent_system_prompt`
    so the meeting caller, the routed-responder proposer, and the
    group chat all build identical persona prompts (role + personality
    preamble plus the ``<task-data>`` / ``<peer-contribution>``
    untrusted-content directive).

    Returns:
        The rendered system prompt string, including the untrusted-
        content directive.
    """
    return render_agent_system_prompt(identity)


class MeetingAgentCallerNotConfiguredError(DomainError):
    """Raised when a meeting runs without an agent + provider registry.

    The meeting orchestrator is structurally wired at construction
    time so the REST surface is never 503, but calling an agent
    requires the
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

    default_message: ClassVar[str] = (
        "Meeting agent caller missing wire-time dependencies"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500

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
        meeting_id: str,
    ) -> AgentResponse:
        """Reject every call: the meeting caller is unconfigured.

        Raises:
            MeetingAgentCallerNotConfiguredError: Always; the required
                dependencies are missing.
        """
        logger.warning(
            MEETING_AGENT_CALL_FAILED,
            agent_id=agent_id,
            meeting_id=meeting_id,
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
