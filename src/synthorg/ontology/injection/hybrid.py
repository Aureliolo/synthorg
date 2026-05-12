"""Hybrid ontology injection strategy.

Combines prompt injection (core entities as system message) with
tool-based access (extended entities via ``lookup_entity``).  This
is the default and recommended strategy.
"""

from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger
from synthorg.observability.events.ontology import ONTOLOGY_INJECTION_PREPARED
from synthorg.ontology.injection.prompt import PromptInjectionStrategy
from synthorg.ontology.injection.tool import (
    LOOKUP_ENTITY_TOOL_NAME,
    LookupEntityTool,
    ToolBasedInjectionStrategy,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.memory.injection import TokenEstimator
    from synthorg.ontology.protocol import OntologyBackend
    from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)
_DEFAULT_CORE_TOKEN_BUDGET: Final[int] = 2000


class HybridInjectionStrategy:
    """Core entities via prompt injection, extended entities via tool.

    Composes ``PromptInjectionStrategy`` and
    ``ToolBasedInjectionStrategy`` to provide both immediate context
    (core entities in the system prompt) and on-demand retrieval
    (all entities via the ``lookup_entity`` tool).

    Args:
        backend: Ontology backend for entity retrieval.
        core_token_budget: Maximum tokens for core entity injection.
        tool_name: Override the default tool name.
        token_estimator: Token estimation implementation.
    """

    def __init__(
        self,
        *,
        backend: OntologyBackend,
        core_token_budget: int = _DEFAULT_CORE_TOKEN_BUDGET,
        tool_name: str = LOOKUP_ENTITY_TOOL_NAME,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self._prompt = PromptInjectionStrategy(
            backend=backend,
            core_token_budget=core_token_budget,
            token_estimator=token_estimator,
        )
        self._tool = ToolBasedInjectionStrategy(
            backend=backend,
            tool_name=tool_name,
        )

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        task_context: NotBlankStr,
        token_budget: int,
    ) -> tuple[ChatMessage, ...]:
        """Build system message with core entities.

        Delegates to the prompt strategy for core entity injection.

        Args:
            agent_id: The agent requesting ontology context.
            task_context: Current task description.
            token_budget: Maximum tokens for ontology content.

        Returns:
            System message with core entity definitions.
        """
        messages = await self._prompt.prepare_messages(
            agent_id,
            task_context,
            token_budget,
        )
        logger.debug(
            ONTOLOGY_INJECTION_PREPARED,
            agent_id=agent_id,
            message_count=len(messages),
            strategy="hybrid",
        )
        return messages

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return the ``lookup_entity`` tool definition.

        Returns:
            Single-element tuple with the tool definition.
        """
        return self._tool.get_tool_definitions()

    @property
    def strategy_name(self) -> str:
        """Return ``"hybrid"``."""
        return "hybrid"

    @property
    def tool(self) -> LookupEntityTool:
        """Return the underlying ``LookupEntityTool`` instance."""
        return self._tool.tool
