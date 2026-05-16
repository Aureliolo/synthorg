"""Layered drift detection strategy.

Applies different sub-strategies per entity tier: active detection
for CORE entities, passive monitoring for USER entities.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.ontology import ONTOLOGY_DRIFT_CHECK_COMPLETED
from synthorg.ontology.models import EntityTier

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.ontology.drift.protocol import DriftDetectionStrategy
    from synthorg.ontology.models import DriftReport
    from synthorg.ontology.protocol import OntologyBackend

logger = get_logger(__name__)


class LayeredDetectionStrategy:
    """Tier-based drift detection.

    Runs the ``active`` sub-strategy for CORE entities and the
    ``passive`` sub-strategy for USER entities.  Requires an
    ``OntologyBackend`` to look up entity tiers.

    Args:
        ontology: Ontology backend for tier lookup.
        core_strategy: Strategy for CORE entities.
        user_strategy: Strategy for USER entities.
    """

    __slots__ = ("_core_strategy", "_ontology", "_user_strategy")

    def __init__(
        self,
        *,
        ontology: OntologyBackend,
        core_strategy: DriftDetectionStrategy,
        user_strategy: DriftDetectionStrategy,
    ) -> None:
        self._ontology = ontology
        self._core_strategy = core_strategy
        self._user_strategy = user_strategy

    async def detect(
        self,
        entity_name: NotBlankStr,
        sample_agents: tuple[NotBlankStr, ...],
    ) -> DriftReport:
        """Run tier-appropriate strategy.

        Args:
            entity_name: Entity to check.
            sample_agents: Agent IDs to sample.

        Returns:
            Drift report from the tier-appropriate strategy.
        """
        entity = await self._ontology.get(entity_name)
        if entity is None:
            logger.warning(
                ONTOLOGY_DRIFT_CHECK_COMPLETED,
                entity_name=entity_name,
                reason="entity_not_found_using_user_strategy",
            )
            return await self._user_strategy.detect(
                entity_name,
                sample_agents,
            )

        if entity.tier == EntityTier.CORE:
            return await self._core_strategy.detect(
                entity_name,
                sample_agents,
            )
        return await self._user_strategy.detect(
            entity_name,
            sample_agents,
        )

    @property
    def strategy_name(self) -> str:
        """Return ``"layered"``."""
        return "layered"
