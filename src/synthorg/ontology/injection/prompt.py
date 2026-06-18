"""Prompt-based ontology injection strategy.

Injects core-tier entity definitions as a system message section,
similar to how ``ContextInjectionStrategy`` injects memory context.
Respects the configured ``core_token_budget``.
"""

from typing import Final

from synthorg.core.text_estimation import DefaultTokenEstimator
from synthorg.core.types import NotBlankStr
from synthorg.memory.injection import TokenEstimator
from synthorg.observability import get_logger
from synthorg.observability.events.ontology import ONTOLOGY_INJECTION_PREPARED
from synthorg.ontology.models import EntityDefinition, EntityTier
from synthorg.persistence.ontology_protocol import OntologyEntityRepository
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)
_DEFAULT_CORE_TOKEN_BUDGET: Final[int] = 2000


def format_entity(entity: EntityDefinition) -> str:
    """Format a single entity definition as readable text.

    Args:
        entity: The entity definition to format.

    Returns:
        Formatted entity text block.
    """
    lines = [f"## {entity.name}"]
    if entity.definition:
        lines.append(entity.definition)
    if entity.fields:
        lines.append("Fields:")
        for field in entity.fields:
            desc = f" -- {field.description}" if field.description else ""
            lines.append(f"  - {field.name}: {field.type_hint}{desc}")
    if entity.constraints:
        lines.append("Constraints:")
        lines.extend(f"  - {constraint}" for constraint in entity.constraints)
    if entity.disambiguation:
        lines.append(f"Not: {entity.disambiguation}")
    if entity.relationships:
        lines.append("Relationships:")
        for rel in entity.relationships:
            desc = f" -- {rel.description}" if rel.description else ""
            lines.append(f"  - {rel.relation} -> {rel.target}{desc}")
    return "\n".join(lines)


class PromptInjectionStrategy:
    """Inject core-tier entity definitions as a system message.

    Retrieves all CORE-tier entities from the ontology backend and
    formats them into a system message section.  Respects the
    configured ``core_token_budget`` by truncating entities that
    exceed the budget.

    Args:
        backend: Ontology backend for entity retrieval.
        core_token_budget: Maximum tokens for injected content.
        token_estimator: Token estimation implementation.
    """

    def __init__(
        self,
        *,
        backend: OntologyEntityRepository,
        core_token_budget: int = _DEFAULT_CORE_TOKEN_BUDGET,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self._backend = backend
        self._core_token_budget = core_token_budget
        self._estimator = token_estimator or DefaultTokenEstimator()

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        task_context: NotBlankStr,  # noqa: ARG002
        token_budget: int,
    ) -> tuple[ChatMessage, ...]:
        """Build a system message containing core entity definitions.

        Entities are formatted and appended until the token budget is
        exhausted.  The effective budget is the minimum of the
        configured ``core_token_budget`` and the caller's
        ``token_budget``.

        Args:
            agent_id: The agent requesting ontology context.
            task_context: Current task description (unused by prompt
                strategy -- all core entities are injected).
            token_budget: Maximum tokens from the caller.

        Returns:
            A single-element tuple with the system message, or empty
            tuple if no entities are available.
        """
        effective_budget = min(self._core_token_budget, token_budget)
        entities = await self._backend.list_entities(tier=EntityTier.CORE)
        if not entities:
            return ()

        header = "# Entity Definitions (Canonical)\n"
        header_tokens = self._estimator.estimate_tokens(header)
        remaining = effective_budget - header_tokens
        if remaining <= 0:
            return ()

        sections: list[str] = [header]
        included = 0
        for entity in entities:
            formatted = format_entity(entity)
            tokens = self._estimator.estimate_tokens(formatted)
            if tokens > remaining:
                break
            sections.append(formatted)
            remaining -= tokens
            included += 1

        if included == 0:
            return ()

        content = "\n\n".join(sections)
        logger.debug(
            ONTOLOGY_INJECTION_PREPARED,
            agent_id=agent_id,
            entity_count=included,
            strategy="prompt",
        )
        return (ChatMessage(role=MessageRole.SYSTEM, content=content),)

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Prompt strategy provides no tools.

        Returns:
            Empty tuple.
        """
        return ()

    @property
    def strategy_name(self) -> str:
        """Return ``"prompt"``."""
        return "prompt"
