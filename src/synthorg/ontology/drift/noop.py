"""No-op drift detection strategy."""

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.ontology.models import DriftAction, DriftReport

logger = get_logger(__name__)


class NoDriftDetection:
    """Returns a clean drift report.  Used when ``strategy: none``."""

    async def detect(
        self,
        entity_name: NotBlankStr,
        sample_agents: tuple[NotBlankStr, ...],  # noqa: ARG002
    ) -> DriftReport:
        """Return a clean report with zero divergence.

        Args:
            entity_name: Entity being checked.
            sample_agents: Agent IDs (unused).

        Returns:
            Clean drift report.
        """
        return DriftReport(
            entity_name=entity_name,
            divergence_score=0.0,
            canonical_version=1,
            recommendation=DriftAction.NO_ACTION,
        )

    @property
    def strategy_name(self) -> str:
        """Return ``"none"``."""
        return "none"
