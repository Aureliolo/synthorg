"""Identity criteria decomposer -- one probe per criterion."""

from typing import TYPE_CHECKING

from synthorg.engine.quality.verification import AtomicProbe
from synthorg.observability import get_logger
from synthorg.observability.events.verification import (
    VERIFICATION_CRITERIA_DECOMPOSED,
)

if TYPE_CHECKING:
    from synthorg.core.task import AcceptanceCriterion
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class IdentityCriteriaDecomposer:
    """Deterministic decomposer that emits one probe per criterion.

    Used in tests and as a no-LLM fallback.
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "identity"

    async def decompose(
        self,
        criteria: tuple[AcceptanceCriterion, ...],
        *,
        task_id: NotBlankStr,
        agent_id: NotBlankStr,  # noqa: ARG002
    ) -> tuple[AtomicProbe, ...]:
        """Map each criterion to a single binary probe.

        Returns:
            Tuple of :class:`AtomicProbe` records, one per
            acceptance criterion in the original order.
        """
        probes = tuple(
            AtomicProbe(
                id=f"{task_id}-probe-{i}",
                probe_text=f"Is the criterion satisfied: {c.description}",
                source_criterion=c.description,
            )
            for i, c in enumerate(criteria)
        )
        logger.info(
            VERIFICATION_CRITERIA_DECOMPOSED,
            task_id=task_id,
            probe_count=len(probes),
            decomposer=self.name,
        )
        return probes
