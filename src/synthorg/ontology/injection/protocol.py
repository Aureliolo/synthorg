"""Ontology injection strategy protocol.

Defines the pluggable ``OntologyInjectionStrategy`` protocol that
controls how entity definitions are made available to agents during
execution.  Mirrors the ``MemoryInjectionStrategy`` protocol from
``synthorg.memory.injection``.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.providers.models import ChatMessage, ToolDefinition


# audit-keep 2026-05-10: injection/factory.py dispatch (prompt/tool/hybrid/
# memory) + agent_engine.py consumer.
@runtime_checkable
class OntologyInjectionStrategy(Protocol):
    """Pluggable strategy for making entity definitions available to agents.

    Implementations determine *how* entity definitions reach the agent:

    - **Prompt**: core entities injected as system message section.
    - **Tool**: on-demand retrieval via ``lookup_entity`` tool.
    - **Hybrid**: core entities via prompt, extended via tool.
    - **Memory**: relies on OrgMemory sync; no direct injection.
    """

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        task_context: NotBlankStr,
        token_budget: int,
    ) -> tuple[ChatMessage, ...]:
        """Return ontology messages to inject into agent context.

        Prompt-based returns a system message with entity definitions.
        Tool-based may return empty (tools handle retrieval).
        Memory-based returns empty (OrgMemory surfaces definitions).

        Args:
            agent_id: The agent requesting ontology context.
            task_context: Current task description for relevance.
            token_budget: Maximum tokens for ontology content.

        Returns:
            Tuple of ``ChatMessage`` instances (may be empty).
        """
        ...

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return tool definitions this strategy provides.

        Prompt-based returns ``()``.  Tool-based returns the
        ``lookup_entity`` tool definition.

        Returns:
            Tuple of ``ToolDefinition`` instances.
        """
        ...

    @property
    def strategy_name(self) -> str:
        """Human-readable strategy identifier.

        Returns:
            Strategy name string.
        """
        ...
