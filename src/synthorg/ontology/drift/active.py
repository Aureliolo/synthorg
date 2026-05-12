"""Active drift validation strategy.

Same logic as passive but intended to run at delegation time
(triggered by ``EntityAlignmentGuard`` in validate/enforce mode).
"""

from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger
from synthorg.ontology.drift.passive import PassiveMonitorStrategy

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.ontology.protocol import OntologyBackend

logger = get_logger(__name__)
_DEFAULT_THRESHOLD: Final[float] = 0.3


class ActiveValidatorStrategy(PassiveMonitorStrategy):
    """Active drift validation -- same detection as passive.

    Distinguished by ``strategy_name`` so callers can differentiate
    in logs and configuration.  Typically wired to run on-demand
    rather than as a background task.

    Args:
        ontology: Ontology backend for definitions.
        memory: Memory backend for agent memories.
        threshold: Divergence threshold for recommendations.
    """

    def __init__(
        self,
        *,
        ontology: OntologyBackend,
        memory: MemoryBackend,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        super().__init__(
            ontology=ontology,
            memory=memory,
            threshold=threshold,
        )

    @property
    def strategy_name(self) -> str:
        """Return ``"active"``."""
        return "active"
