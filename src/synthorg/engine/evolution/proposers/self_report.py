"""Heuristic-based proposer for agent self-reporting adaptations.

Lighter proposer for success-path evolution. Generates proposals based
on agent's own recent task results and procedural memories without a
separate LLM call.

Never proposes identity axis (too risky for self-report).
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_PROPOSER_ANALYZE,
    EVOLUTION_PROPOSER_INIT,
)
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.engine.evolution.protocols import EvolutionContext

logger = get_logger(__name__)
_DEFAULT_TEMPERATURE: Final[float] = 0.3
_DEFAULT_MAX_TOKENS: Final[int] = 1000

_HIGH_QUALITY_THRESHOLD: Final[float] = 9.0
"""Quality score threshold for strategy adaptation proposals."""


class SelfReportProposer:
    """Heuristic-based proposer for agent self-report evolution.

    Generates proposals based on performance data and procedural memories
    without a separate LLM call. Conservative: only proposes
    STRATEGY_SELECTION and PROMPT_TEMPLATE axes.

    Args:
        provider: Completion provider (for future expansion; not used yet).
        model: Model identifier (for future expansion; not used yet).
        temperature: Sampling temperature (unused, for API consistency).
        max_tokens: Maximum tokens (unused, for API consistency).
    """

    def __init__(
        self,
        provider: CompletionProvider,
        *,
        model: str,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._provider = provider
        self._model = model
        logger.debug(
            EVOLUTION_PROPOSER_INIT,
            proposer="self_report",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @property
    def name(self) -> str:
        """Human-readable proposer name."""
        return "self_report"

    async def propose(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,
    ) -> tuple[AdaptationProposal, ...]:
        """Generate zero or more adaptation proposals based on heuristics.

        Args:
            agent_id: Agent to generate proposals for.
            context: Evolution context with identity, performance,
                and memory data.

        Returns:
            Tuple of proposals (empty if no adaptations suggested).
        """
        proposals: list[AdaptationProposal] = []

        # No performance data -> no proposals.
        if context.performance_snapshot is None:
            logger.debug(
                EVOLUTION_PROPOSER_ANALYZE,
                agent_id=str(agent_id),
                proposer="self_report",
                reason="no_performance_snapshot",
            )
            return ()

        # High quality (>9.0) -> suggest strategy selection.
        if (
            context.performance_snapshot.overall_quality_score is not None
            and context.performance_snapshot.overall_quality_score
            > _HIGH_QUALITY_THRESHOLD
        ):
            proposal = AdaptationProposal(
                agent_id=agent_id,
                axis=AdaptationAxis.STRATEGY_SELECTION,
                description=NotBlankStr(
                    "Strategy adaptation for sustained high performance"
                ),
                changes={"strategy": "explore_alternative_approaches"},
                confidence=0.6,
                source=AdaptationSource.SUCCESS,
            )
            proposals.append(proposal)
            logger.debug(
                EVOLUTION_PROPOSER_ANALYZE,
                agent_id=str(agent_id),
                axis="strategy_selection",
                reason="high_quality_score",
                quality=context.performance_snapshot.overall_quality_score,
            )

        # Recent procedural memories -> inject into prompt template.
        if context.recent_procedural_memories:
            memory_ids = [str(mem.id) for mem in context.recent_procedural_memories]
            proposal = AdaptationProposal(
                agent_id=agent_id,
                axis=AdaptationAxis.PROMPT_TEMPLATE,
                description=NotBlankStr(
                    "Inject procedural memories into prompt template"
                ),
                changes={
                    "template_injection": "procedural_memories",
                    "memory_ids": memory_ids,
                },
                confidence=0.75,
                source=AdaptationSource.SUCCESS,
            )
            proposals.append(proposal)
            logger.debug(
                EVOLUTION_PROPOSER_ANALYZE,
                agent_id=str(agent_id),
                axis="prompt_template",
                reason="procedural_memories_available",
                memory_count=len(memory_ids),
            )

        return tuple(proposals)
