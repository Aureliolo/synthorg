"""Passive drift monitoring strategy.

Queries agent memories for entity-tagged entries and computes
divergence via keyword overlap between stored content and the
canonical entity definition.
"""

from typing import Final

from synthorg.core.text_similarity import tokenize_words, word_overlap
from synthorg.core.types import NotBlankStr
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_DRIFT_CHECK_COMPLETED,
    ONTOLOGY_DRIFT_CHECK_STARTED,
)
from synthorg.ontology.errors import OntologyNotFoundError
from synthorg.ontology.models import AgentDrift, DriftAction, DriftReport
from synthorg.persistence.ontology_protocol import OntologyEntityRepository

logger = get_logger(__name__)


_NOTIFY_THRESHOLD: Final[float] = 0.5
_RETRAIN_THRESHOLD: Final[float] = 0.7
_DEFAULT_DRIFT_DETECT_THRESHOLD: Final[float] = 0.3

# Per-agent memory sample size when scoring passive entity drift.
_DRIFT_MEMORY_SAMPLE_LIMIT: Final[int] = 20


def _recommend(score: float, threshold: float) -> DriftAction:
    """Map divergence score to recommendation.

    Args:
        score: Aggregate divergence score.
        threshold: Configured drift threshold.

    Returns:
        Recommended action.
    """
    if score < threshold:
        return DriftAction.NO_ACTION
    if score < _NOTIFY_THRESHOLD:
        return DriftAction.NOTIFY
    if score < _RETRAIN_THRESHOLD:
        return DriftAction.RETRAIN
    return DriftAction.ESCALATE


class PassiveMonitorStrategy:
    """Passive drift detection via keyword overlap.

    Queries each sampled agent's memory for entity-tagged entries,
    then computes divergence as ``1 - keyword_overlap`` between
    stored content and the canonical definition.

    Args:
        ontology: Ontology backend for definitions.
        memory: Memory backend for agent memories.
        threshold: Divergence threshold for recommendations.
    """

    __slots__ = ("_memory", "_ontology", "_threshold")

    def __init__(
        self,
        *,
        ontology: OntologyEntityRepository,
        memory: MemoryBackend,
        threshold: float = _DEFAULT_DRIFT_DETECT_THRESHOLD,
    ) -> None:
        self._ontology = ontology
        self._memory = memory
        self._threshold = threshold

    async def detect(
        self,
        entity_name: NotBlankStr,
        sample_agents: tuple[NotBlankStr, ...],
    ) -> DriftReport:
        """Detect drift for a single entity across sampled agents.

        Args:
            entity_name: Entity to check.
            sample_agents: Agent IDs to sample.

        Returns:
            Drift report with per-agent divergence details.
        """
        from synthorg.memory.models import MemoryQuery  # noqa: PLC0415

        logger.debug(
            ONTOLOGY_DRIFT_CHECK_STARTED,
            entity_name=entity_name,
            agent_count=len(sample_agents),
            strategy="passive",
        )

        try:
            entity = await self._ontology.get(entity_name)
        except OntologyNotFoundError:
            entity = None
        if entity is None:
            logger.warning(
                ONTOLOGY_DRIFT_CHECK_COMPLETED,
                entity_name=entity_name,
                divergence_score=0.0,
                reason="entity_not_found",
            )
            return DriftReport(
                entity_name=entity_name,
                divergence_score=0.0,
                canonical_version=1,
                recommendation=DriftAction.NO_ACTION,
            )

        manifest = await self._ontology.get_version_manifest()
        version = manifest.get(entity_name, 1)
        canonical_text = entity.definition or entity.name

        agent_drifts: list[AgentDrift] = []
        agent_scores: list[float] = []
        for agent_id in sample_agents:
            query = MemoryQuery(
                tags=(f"entity:{entity_name}",),
                limit=_DRIFT_MEMORY_SAMPLE_LIMIT,
            )
            entries = await self._memory.retrieve(agent_id, query)
            if not entries:
                continue

            combined = " ".join(e.content for e in entries)
            overlap = word_overlap(
                tokenize_words(combined), tokenize_words(canonical_text)
            )
            divergence = round(1.0 - overlap, 3)
            agent_scores.append(divergence)
            if divergence > 0.0:
                agent_drifts.append(
                    AgentDrift(
                        agent_id=agent_id,
                        divergence_score=divergence,
                        details=f"Keyword overlap: {overlap:.1%}",
                    ),
                )

        agg = sum(agent_scores) / len(agent_scores) if agent_scores else 0.0

        report = DriftReport(
            entity_name=entity_name,
            divergence_score=round(agg, 3),
            divergent_agents=tuple(agent_drifts),
            canonical_version=version,
            recommendation=_recommend(agg, self._threshold),
        )
        logger.debug(
            ONTOLOGY_DRIFT_CHECK_COMPLETED,
            entity_name=entity_name,
            divergence_score=report.divergence_score,
            recommendation=report.recommendation.value,
        )
        return report

    @property
    def strategy_name(self) -> str:
        """Return ``"passive"``."""
        return "passive"
