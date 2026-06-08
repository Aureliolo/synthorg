"""StrategySelectionAdapter -- stores strategy preferences as procedural memory."""

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.adapters._memory_store import (
    store_proposal_as_memory,
)
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import EVOLUTION_ADAPTED

logger = get_logger(__name__)


class StrategySelectionAdapter:
    """Stores strategy preferences as procedural memory.

    Converts a strategy selection adaptation into a procedural memory entry
    with the tag "evolution-strategy" for later retrieval and reuse.
    """

    def __init__(self, memory_backend: MemoryBackend) -> None:
        """Initialize StrategySelectionAdapter.

        Args:
            memory_backend: Memory storage backend.
        """
        self._memory_backend = memory_backend

    @property
    def name(self) -> str:
        """Return adapter name."""
        return "StrategySelectionAdapter"

    @property
    def axis(self) -> AdaptationAxis:
        """Return the adaptation axis this adapter handles."""
        return AdaptationAxis.STRATEGY_SELECTION

    async def apply(
        self,
        proposal: AdaptationProposal,
        agent_id: NotBlankStr,
    ) -> None:
        """Apply the strategy selection adaptation.

        Stores the proposal as a procedural memory entry tagged with
        "evolution-strategy" for organizational use.

        Args:
            proposal: The approved proposal to apply.
            agent_id: Target agent.

        Raises:
            Exception: If the memory store operation fails.
        """
        try:
            await store_proposal_as_memory(
                self._memory_backend,
                proposal,
                agent_id,
                "evolution-strategy",
            )
        except Exception as exc:
            logger.warning(
                EVOLUTION_ADAPTED,
                adapter=self.name,
                axis=self.axis.value,
                agent_id=agent_id,
                proposal_id=str(proposal.id),
                status="failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            EVOLUTION_ADAPTED,
            adapter=self.name,
            axis=self.axis.value,
            agent_id=agent_id,
            proposal_id=str(proposal.id),
            status="succeeded",
        )
