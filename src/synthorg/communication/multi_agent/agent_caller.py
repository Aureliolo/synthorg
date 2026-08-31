""":data:`AgentCaller` factory for multi-party conversations.

A conversation invokes agents through an :data:`AgentCaller` callable.
:func:`build_agent_caller` composes an agent registry (for identity
lookup) with a :class:`ProviderRegistry` (for LLM dispatch) and runs one
``provider.complete()`` call per invocation.

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
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr, require_not_blank
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.agent_sampling import resolve_sampling
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.multi_agent import (
    MULTI_AGENT_CALL_FAILED,
    MULTI_AGENT_CALLED,
    MULTI_AGENT_RESPONDED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


class UnknownConversationAgentError(NotFoundError):
    """Raised when a conversation invokes an unregistered agent.

    Attributes:
        agent_id: The agent identifier that was not found in the registry.

    Raises:
        ValueError: If *agent_id* is blank. The annotation alone binds
            only inside a Pydantic model.
    """

    default_message: ClassVar[str] = "Conversation agent not registered"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404

    def __init__(self, agent_id: NotBlankStr) -> None:
        require_not_blank(agent_id, "agent_id")
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
            ValueError: If either identifier is blank. ``NotBlankStr`` only
                binds inside a Pydantic model, so both are checked here or
                a blank one reaches the registry lookup and the cost row
                unexamined.
        """
        typed_agent_id = require_not_blank(agent_id, "agent_id")
        cleaned_conversation_id = require_not_blank(
            conversation_id.strip(),
            "conversation_id",
        )
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
        config = resolve_sampling(identity).model_copy(
            update={"max_tokens": effective_max_tokens}
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

    The system message is derived from the agent identity (name, role and
    department) so the LLM stays in character across the
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


__all__ = [
    "UnknownConversationAgentError",
    "build_agent_caller",
]
