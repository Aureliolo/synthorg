""":data:`AgentCaller` factories for multi-party conversations.

A conversation invokes agents through an :data:`AgentCaller` callable.
This module provides the two implementations a composition root picks
between:

- :func:`build_agent_caller` -- the real caller. Composes an agent
  registry (for identity lookup) with a :class:`ProviderRegistry` (for
  LLM dispatch) and runs one ``provider.complete()`` call per
  invocation.
- :func:`build_unconfigured_agent_caller` -- built when those registries
  are not available at wire time. It raises at call time rather than
  answering with empty content, so an operator sees the absent
  collaborator instead of a conversation that ran and said nothing.

One turn is one LLM call. Sequencing turns belongs to the conversation;
this module runs a single agent's inference.
"""

from typing import ClassVar

from synthorg.budget.call_category import LLMCallCategory

# ``CostTrackerProtocol``, ``AgentRegistryProtocol``, ``ProviderRegistry``
# and ``AgentCaller`` are part of the public ``build_agent_caller``
# signature so they must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).  Importing at
# module top -- not under ``TYPE_CHECKING`` -- keeps the names in
# module globals.
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.communication.multi_agent.models import AgentResponse
from synthorg.communication.multi_agent.protocol import AgentCaller
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError, NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.multi_agent import (
    MULTI_AGENT_CALL_FAILED,
    MULTI_AGENT_CALLED,
    MULTI_AGENT_RESPONDED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class UnknownConversationAgentError(NotFoundError):
    """Raised when a conversation invokes an unregistered agent.

    Attributes:
        agent_id: The agent identifier that was not found in the registry.
    """

    default_message: ClassVar[str] = "Conversation agent not registered"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404

    def __init__(self, agent_id: NotBlankStr) -> None:
        super().__init__(
            f"Agent {agent_id!r} is not registered in the agent registry; "
            f"cannot dispatch LLM call"
        )
        self.agent_id: NotBlankStr = agent_id


def build_agent_caller(
    *,
    agent_registry: AgentRegistryProtocol,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None = None,
) -> AgentCaller:
    """Construct an :data:`AgentCaller` backed by real services.

    Args:
        agent_registry: Source of truth for agent identities.
        provider_registry: Source of truth for LLM providers.
        cost_tracker: Optional cost tracker; when wired each turn
            records via the chokepoint.

    Returns:
        An async callback matching the :data:`AgentCaller` contract.
    """

    async def _caller(
        agent_id: str,
        prompt: str,
        max_tokens: int,
        conversation_id: str,
    ) -> AgentResponse:
        """Invoke the agent's provider for one turn.

        Returns:
            The agent's response with token usage and cost.

        Raises:
            UnknownConversationAgentError: If the agent id is not registered.
        """
        typed_agent_id = NotBlankStr(agent_id)
        # Validate the conversation id at the call boundary so a blank /
        # whitespace-only id surfaces as a clean ``ValueError`` here rather
        # than as a generic NotBlankStr failure inside
        # ``cost_recording_scope``.
        cleaned_conversation_id = NotBlankStr(conversation_id.strip())
        logger.info(
            MULTI_AGENT_CALLED,
            agent_id=agent_id,
            conversation_id=cleaned_conversation_id,
            max_tokens=max_tokens,
            prompt_length=len(prompt),
        )
        identity = await agent_registry.get(typed_agent_id)
        if identity is None:
            logger.warning(
                MULTI_AGENT_CALL_FAILED,
                agent_id=agent_id,
                conversation_id=cleaned_conversation_id,
                error_type="UnknownConversationAgentError",
            )
            raise UnknownConversationAgentError(typed_agent_id)

        provider_name = str(identity.model.provider)
        provider = provider_registry.get(provider_name)
        messages = _build_messages(identity, prompt)
        # The conversation's own cap still binds; the agent's binding only
        # tightens it further when an operator set one, and answers nothing
        # when unset.
        own = identity.model.max_tokens
        effective_max_tokens = max_tokens if own is None else min(max_tokens, own)
        config = CompletionConfig(
            temperature=identity.model.temperature,
            max_tokens=effective_max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=cost_tracker,
                agent_id=typed_agent_id,
                # Multi-agent coordination is not a registered system
                # prompt class.
                purpose=None,
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
                MULTI_AGENT_CALL_FAILED,
                agent_id=agent_id,
                provider=provider_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
            MULTI_AGENT_RESPONDED,
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
    """Assemble the minimal ``system`` + ``user`` pair for one turn.

    The system message is derived from the agent identity (role +
    personality traits) so the LLM stays in character across the
    conversation. Callers inject the full turn context into ``prompt``,
    so the system prompt only carries agent-stable identity.

    Returns:
        The ``system`` + ``user`` message pair for the turn.
    """
    return [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content=render_agent_system_prompt(identity),
        ),
        ChatMessage(role=MessageRole.USER, content=prompt),
    ]


class AgentCallerNotConfiguredError(DomainError):
    """Raised when a conversation runs without an agent + provider registry.

    Calling an agent requires the agent registry (for identity lookup)
    and the provider registry (for LLM dispatch). When either is absent
    at wire time, a conversation that tries to invoke an agent receives
    this error rather than an empty response.

    Attributes:
        agent_id: The agent identifier the conversation tried to invoke.
        missing_dependencies: Names of the dependencies that were
            absent at wire time (e.g. ``("agent_registry",
            "provider_registry")``).  Guaranteed non-empty: the error
            is only meaningful when at least one dependency is missing.

    Raises:
        ValueError: If *missing_dependencies* is empty -- the error is
            only meaningful when at least one dependency is missing.
    """

    default_message: ClassVar[str] = "Agent caller missing wire-time dependencies"
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
                "AgentCallerNotConfiguredError requires at least one "
                "entry in missing_dependencies"
            )
            raise ValueError(msg)
        missing = ", ".join(missing_dependencies)
        super().__init__(
            f"Agent caller invoked for {agent_id!r} but the following "
            f"dependencies were missing at wire time: {missing}.  Provide "
            f"them via create_app(...) so turns can dispatch real LLM calls."
        )
        self.agent_id: NotBlankStr = agent_id
        self.missing_dependencies: tuple[str, ...] = missing_dependencies


class UnconfiguredAgentCaller:
    """An :data:`AgentCaller` that refuses every turn, naming what is absent.

    A named type rather than a closure because the holder of one is an
    observable fact about the deployment: it can serve reads and cannot
    run a conversation. A holder answers that from the caller itself, so
    a probe cannot claim dispatch a boot never installed.

    Attributes:
        missing_dependencies: The collaborators absent when it was built.
    """

    __slots__ = ("missing_dependencies",)

    def __init__(self, *, missing_dependencies: tuple[str, ...]) -> None:
        """Bind the dependency names the refusal reports.

        Checked here rather than in the factory alone, because this is the
        one place every construction passes through. An instance built with
        nothing missing refuses every turn while naming no reason, and the
        refusal it raises validates the same tuple, so the only report an
        operator gets arrives at the first turn instead of at wire time and
        names the caller rather than the absent collaborator.

        Args:
            missing_dependencies: Names of the dependencies missing at wire
                time. Must be non-empty.

        Raises:
            ValueError: If *missing_dependencies* is empty.
        """
        if not missing_dependencies:
            msg = (
                "UnconfiguredAgentCaller requires at least one entry in "
                "missing_dependencies"
            )
            raise ValueError(msg)
        self.missing_dependencies: tuple[str, ...] = missing_dependencies

    async def __call__(
        self,
        agent_id: str,
        _prompt: str,
        _max_tokens: int,
        conversation_id: str,
    ) -> AgentResponse:
        """Reject the turn.

        Args:
            agent_id: The agent whose turn it would have been.
            _prompt: Unused; nothing is dispatched.
            _max_tokens: Unused; nothing is dispatched.
            conversation_id: The conversation the turn belongs to.

        Raises:
            AgentCallerNotConfiguredError: Always; the required
                dependencies are missing.
        """
        logger.warning(
            MULTI_AGENT_CALL_FAILED,
            agent_id=agent_id,
            conversation_id=conversation_id,
            error_type="AgentCallerNotConfiguredError",
            missing_dependencies=self.missing_dependencies,
        )
        raise AgentCallerNotConfiguredError(
            agent_id=NotBlankStr(agent_id),
            missing_dependencies=self.missing_dependencies,
        )


def build_unconfigured_agent_caller(
    *,
    missing_dependencies: tuple[str, ...],
) -> AgentCaller:
    """Return a caller that raises loudly if invoked.

    Used when a conversation is wired before the agent / provider
    registries are available. Surfaces the root cause to operators at
    first use rather than silently succeeding with empty content.

    Args:
        missing_dependencies: Names of the dependencies missing at wire
            time.  Must be non-empty.

    Returns:
        A refusing caller naming *missing_dependencies*.

    Raises:
        ValueError: If *missing_dependencies* is empty.
    """
    if not missing_dependencies:
        msg = (
            "build_unconfigured_agent_caller requires at least one "
            "entry in missing_dependencies"
        )
        raise ValueError(msg)

    return UnconfiguredAgentCaller(missing_dependencies=missing_dependencies)


__all__ = [
    "AgentCallerNotConfiguredError",
    "UnconfiguredAgentCaller",
    "UnknownConversationAgentError",
    "build_agent_caller",
    "build_unconfigured_agent_caller",
]
