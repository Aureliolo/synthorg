"""Composite proposer that routes between failure and success paths.

Routes to failure proposer on declining quality trends, success proposer
otherwise. Returns proposals from the selected path without merging.
"""

from typing import TYPE_CHECKING, Final

from synthorg.engine.evolution.protocols import (
    AdaptationProposer,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_PROPOSER_INIT,
    EVOLUTION_PROPOSER_ROUTE,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.evolution.models import AdaptationProposal
    from synthorg.engine.evolution.protocols import (
        EvolutionContext,
    )

logger = get_logger(__name__)

_QUALITY_DECLINE_THRESHOLD: Final[float] = 5.0
"""Quality score threshold below which failure path is triggered."""


class CompositeProposer:
    """Routes between failure and success proposers based on trajectory.

    If the agent's quality score indicates decline (< 5.0), routes to the
    failure proposer. Otherwise routes to the success proposer.
    Merges proposals from both paths.

    Args:
        failure_proposer: Proposer for failure scenarios.
        success_proposer: Proposer for success/healthy scenarios.
    """

    def __init__(
        self,
        *,
        failure_proposer: AdaptationProposer,
        success_proposer: AdaptationProposer,
    ) -> None:
        self._failure_proposer = failure_proposer
        self._success_proposer = success_proposer
        logger.debug(
            EVOLUTION_PROPOSER_INIT,
            proposer="composite",
            failure_proposer=failure_proposer.name,
            success_proposer=success_proposer.name,
        )

    @property
    def name(self) -> str:
        """Human-readable proposer name."""
        return "composite"

    async def propose(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,
    ) -> tuple[AdaptationProposal, ...]:
        """Generate adaptation proposals by routing to failure or success path.

        Args:
            agent_id: Agent to generate proposals for.
            context: Evolution context with identity, performance,
                and memory data.

        Returns:
            Tuple of proposals (merged from selected path).
        """
        # Determine which path to use based on performance trajectory.
        use_failure_path = self._should_use_failure_path(context)

        if use_failure_path:
            logger.debug(
                EVOLUTION_PROPOSER_ROUTE,
                agent_id=str(agent_id),
                selected_proposer="failure",
            )
            proposals = await self._failure_proposer.propose(
                agent_id=agent_id,
                context=context,
            )
        else:
            logger.debug(
                EVOLUTION_PROPOSER_ROUTE,
                agent_id=str(agent_id),
                selected_proposer="success",
            )
            proposals = await self._success_proposer.propose(
                agent_id=agent_id,
                context=context,
            )

        return proposals

    def _should_use_failure_path(self, context: EvolutionContext) -> bool:
        """Determine if failure path should be used.

        Args:
            context: Evolution context.

        Returns:
            True if failure path should be used, False for success path.
        """
        # No performance data -> use optimistic success path.
        if context.performance_snapshot is None:
            return False

        # Low quality score indicates decline -> use failure path.
        quality = context.performance_snapshot.overall_quality_score
        if quality is None:
            return False

        return quality < _QUALITY_DECLINE_THRESHOLD
