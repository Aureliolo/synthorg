"""Memory-based ontology injection strategy.

Relies on ``OntologyOrgMemorySync`` to publish entity definitions
as ``OrgFact`` entries.  Agents discover definitions through the
existing ``ContextInjectionStrategy`` memory retrieval pipeline.
No direct ontology injection or tool exposure.
"""

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.ontology import ONTOLOGY_INJECTION_PREPARED
from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)


class MemoryBasedInjectionStrategy:
    """No-op injection strategy that relies on OrgMemory sync.

    Entity definitions are published to organizational memory by
    ``OntologyOrgMemorySync`` and surface through the existing
    ``ContextInjectionStrategy`` memory retrieval pipeline.  This
    strategy injects no messages and exposes no tools.
    """

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        task_context: NotBlankStr,  # noqa: ARG002
        token_budget: int,  # noqa: ARG002
    ) -> tuple[ChatMessage, ...]:
        """Memory strategy injects no messages.

        Args:
            agent_id: The agent requesting ontology context.
            task_context: Current task description.
            token_budget: Maximum tokens (unused).

        Returns:
            Empty tuple.
        """
        logger.debug(
            ONTOLOGY_INJECTION_PREPARED,
            agent_id=agent_id,
            entity_count=0,
            strategy="memory",
        )
        return ()

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Memory strategy provides no tools.

        Returns:
            Empty tuple.
        """
        return ()

    @property
    def strategy_name(self) -> str:
        """Return ``"memory"``."""
        return "memory"
