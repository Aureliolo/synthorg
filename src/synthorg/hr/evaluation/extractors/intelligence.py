"""Intelligence/Accuracy pillar metric extractor.

Lifts the CI quality + LLM calibration sub-metrics from the
``EvaluationContext`` snapshot and calibration records. Custom
confidence multiplier applies the calibration-drift penalty:
when ``avg_drift > calibration_drift_threshold`` the multiplier
shrinks confidence linearly down to a floor of ``0.1``.

Composed with ``ConfigurablePillarScorer`` to produce the
intelligence ``PillarScoringStrategy``.
"""

from typing import TYPE_CHECKING

from synthorg.hr.evaluation.constants import MAX_SCORE
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.metric_extractor_protocol import ExtractedMetrics
from synthorg.observability import get_logger
from synthorg.observability.events.evaluation import (
    EVAL_CALIBRATION_DRIFT_HIGH,
    EVAL_METRIC_SKIPPED,
)

if TYPE_CHECKING:
    from synthorg.hr.evaluation.models import EvaluationContext

logger = get_logger(__name__)


class IntelligenceMetricExtractor:
    """Extract CI quality + LLM calibration sub-metrics."""

    __slots__ = ()

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        return EvaluationPillar.INTELLIGENCE

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Read CI quality + LLM calibration and emit sub-metric scores.

        Returns:
            Result of type ``ExtractedMetrics``.
        """
        scores, weights, data_points, calibration_drift = _collect_metrics(context)

        if not weights:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={
                    "reason": "no_enabled_metrics_with_data",
                },
            )

        confidence_multiplier = _drift_confidence_multiplier(
            context,
            calibration_drift,
        )

        return ExtractedMetrics(
            scores=scores,
            weights=weights,
            data_points=data_points,
            confidence_multiplier=confidence_multiplier,
        )


def _collect_metrics(
    context: EvaluationContext,
) -> tuple[dict[str, float], dict[str, float], int, float]:
    """Gather enabled CI quality + LLM calibration sub-metrics.

    ``data_points`` only counts observations that actually contribute
    a score: task records count toward the total exactly when
    ``ci_quality`` is enabled AND a quality score is present;
    calibration records count when ``llm_calibration`` is enabled AND
    at least one record exists. Counting records that did not feed
    into ``scores`` would overstate confidence.

    Args:
        context: Evaluation context with snapshot, task records, and
            calibration records.

    Returns:
        ``(scores, weights, data_points, calibration_drift)``.
    """
    scores: dict[str, float] = {}
    weights: dict[str, float] = {}
    data_points = 0
    calibration_drift = 0.0
    cfg = context.config.intelligence
    ci_score = context.snapshot.overall_quality_score

    if cfg.ci_quality_enabled and ci_score is not None:
        scores["ci_quality"] = ci_score
        weights["ci_quality"] = cfg.ci_quality_weight
        data_points += len(context.task_records)
    elif cfg.ci_quality_enabled:
        logger.debug(
            EVAL_METRIC_SKIPPED,
            agent_id=context.agent_id,
            pillar=EvaluationPillar.INTELLIGENCE.value,
            metric="ci_quality",
            reason="no_quality_score",
        )

    if cfg.llm_calibration_enabled:
        records = context.calibration_records
        if records:
            avg_llm = sum(r.llm_score for r in records) / len(records)
            scores["llm_calibration"] = avg_llm
            weights["llm_calibration"] = cfg.llm_calibration_weight
            calibration_drift = sum(r.drift for r in records) / len(records)
            data_points += len(records)
        else:
            logger.debug(
                EVAL_METRIC_SKIPPED,
                agent_id=context.agent_id,
                pillar=EvaluationPillar.INTELLIGENCE.value,
                metric="llm_calibration",
                reason="no_calibration_records",
            )

    return scores, weights, data_points, calibration_drift


def _drift_confidence_multiplier(
    context: EvaluationContext,
    calibration_drift: float,
) -> float:
    """Compute the multiplier on base confidence from calibration drift.

    Returns:
        Result of type ``float``.
    """
    threshold = context.config.calibration_drift_threshold
    if calibration_drift <= threshold:
        return 1.0
    logger.info(
        EVAL_CALIBRATION_DRIFT_HIGH,
        agent_id=context.agent_id,
        pillar=EvaluationPillar.INTELLIGENCE.value,
        drift=round(calibration_drift, 4),
        threshold=threshold,
    )
    return max(
        0.1,
        1.0 - (calibration_drift - threshold) / MAX_SCORE,
    )
