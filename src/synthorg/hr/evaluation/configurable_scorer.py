"""Composite ``PillarScoringStrategy`` over per-pillar extractors.

The single class ``ConfigurablePillarScorer`` replaces the four
dedicated pillar strategy classes and the inline Efficiency block
in ``evaluator.py``. It composes a per-pillar ``MetricExtractor``
with the shared finalize step (redistribute weights -> weighted
average -> clamp -> confidence -> log -> ``PillarScore``).
"""

from collections.abc import Mapping

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.constants import (
    FULL_CONFIDENCE_DATA_POINTS,
    MAX_SCORE,
    NEUTRAL_SCORE,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.metric_extractor_protocol import (
    ExtractedMetrics,
    MetricExtractor,
)
from synthorg.hr.evaluation.models import (
    EvaluationContext,
    PillarScore,
    redistribute_weights,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evaluation import (
    EVAL_PILLAR_INSUFFICIENT_DATA,
    EVAL_PILLAR_SCORED,
)

logger = get_logger(__name__)


class ConfigurablePillarScorer:
    """Compose ``(pillar, extractor)`` into a ``PillarScoringStrategy``.

    The pillar identity is the closed-enum ``EvaluationPillar``. The
    extractor owns the per-pillar data extraction. This class owns
    the universal "redistribute -> weighted-average -> clamp ->
    confidence -> log" pipeline, so the five pillar strategies all
    share it instead of duplicating ~80 LoC each.
    """

    __slots__ = ("_extractor", "_pillar")

    def __init__(
        self,
        pillar: EvaluationPillar,
        extractor: MetricExtractor,
    ) -> None:
        if extractor.pillar is not pillar:
            msg = (
                f"Extractor pillar {extractor.pillar!r} does not match "
                f"composite pillar {pillar!r}"
            )
            raise ValueError(msg)
        self._pillar = pillar
        self._extractor = extractor

    @property
    def name(self) -> str:
        """Strategy name -- includes the pillar for log clarity."""
        return f"configurable[{self._pillar.value}]"

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this strategy scores."""
        return self._pillar

    async def score(self, *, context: EvaluationContext) -> PillarScore:
        """Run the extractor then the shared finalize step.

        Returns:
            Result of type ``PillarScore``.
        """
        metrics = await self._extractor.extract(context)
        if metrics.insufficient_data:
            self._log_insufficient_data(
                context,
                metrics.insufficient_data_event_kwargs,
            )
            return self._neutral(context, metrics.neutral_data_point_count)
        return self._build_result(context, metrics)

    def _build_result(
        self,
        context: EvaluationContext,
        metrics: ExtractedMetrics,
    ) -> PillarScore:
        """Aggregate enabled metrics into a ``PillarScore``.

        Returns:
            Result of type ``PillarScore``.
        """
        enabled = [(name, w, True) for name, w in metrics.weights.items()]
        weights = redistribute_weights(enabled)
        weighted_sum = sum(metrics.scores[k] * weights[k] for k in weights)
        final_score = max(0.0, min(MAX_SCORE, weighted_sum))

        breakdown = tuple(
            (NotBlankStr(k), round(v, 4)) for k, v in sorted(metrics.scores.items())
        )

        base_confidence = min(
            1.0,
            metrics.data_points / FULL_CONFIDENCE_DATA_POINTS,
        )
        confidence = max(
            0.0,
            min(1.0, base_confidence * metrics.confidence_multiplier),
        )

        result = PillarScore(
            pillar=self._pillar,
            score=round(final_score, 4),
            confidence=round(confidence, 4),
            strategy_name=NotBlankStr(self.name),
            breakdown=breakdown,
            data_point_count=metrics.data_points,
            evaluated_at=context.now,
        )

        logger.debug(
            EVAL_PILLAR_SCORED,
            agent_id=context.agent_id,
            pillar=self._pillar.value,
            score=result.score,
            confidence=result.confidence,
        )
        return result

    def _log_insufficient_data(
        self,
        context: EvaluationContext,
        log_kwargs: Mapping[str, object],
    ) -> None:
        """Emit ``EVAL_PILLAR_INSUFFICIENT_DATA`` with extractor's kwargs."""
        logger.info(
            EVAL_PILLAR_INSUFFICIENT_DATA,
            agent_id=context.agent_id,
            pillar=self._pillar.value,
            **log_kwargs,
        )

    def _neutral(
        self,
        context: EvaluationContext,
        data_point_count: int | None,
    ) -> PillarScore:
        """Return neutral score with zero confidence.

        ``data_point_count`` defaults to ``0``; extractors that want
        to preserve a count in the neutral case (Experience: number
        of feedback records seen even when below threshold) override
        via ``ExtractedMetrics.neutral_data_point_count``.

        Returns:
            Result of type ``PillarScore``.
        """
        return PillarScore(
            pillar=self._pillar,
            score=NEUTRAL_SCORE,
            confidence=0.0,
            strategy_name=NotBlankStr(self.name),
            data_point_count=data_point_count if data_point_count is not None else 0,
            evaluated_at=context.now,
        )
