"""Resilience pillar metric extractor.

Lifts the success-rate / recovery-rate / consistency / streak
sub-metrics from ``EvaluationContext.resilience_metrics``. Composed
with ``ConfigurablePillarScorer`` to produce the resilience
``PillarScoringStrategy``.
"""

from typing import TYPE_CHECKING

from synthorg.hr.evaluation.constants import MAX_SCORE, NEUTRAL_SCORE
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.metric_extractor_protocol import ExtractedMetrics

if TYPE_CHECKING:
    from synthorg.hr.evaluation.config import ResilienceConfig
    from synthorg.hr.evaluation.models import EvaluationContext, ResilienceMetrics


class ResilienceMetricExtractor:
    """Extract success/recovery/consistency/streak metrics."""

    __slots__ = ()

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        return EvaluationPillar.RESILIENCE

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Read ``context.resilience_metrics`` and emit sub-metric scores."""
        rm = context.resilience_metrics
        if rm is None or rm.total_tasks == 0:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={"reason": "no_resilience_metrics"},
            )

        scores, weights = _collect_metrics(context.config.resilience, rm)

        if not weights:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={"reason": "no_enabled_metrics"},
            )

        return ExtractedMetrics(
            scores=scores,
            weights=weights,
            data_points=rm.total_tasks,
        )


def _collect_metrics(
    cfg: ResilienceConfig,
    rm: ResilienceMetrics,
) -> tuple[dict[str, float], dict[str, float]]:
    """Gather enabled resilience sub-metrics with their raw weights."""
    scores: dict[str, float] = {}
    weights: dict[str, float] = {}

    if cfg.success_rate_enabled:
        rate = (rm.total_tasks - rm.failed_tasks) / rm.total_tasks
        scores["success_rate"] = rate * MAX_SCORE
        weights["success_rate"] = cfg.success_rate_weight

    if cfg.recovery_rate_enabled:
        if rm.failed_tasks > 0:
            recovery = min(1.0, rm.recovered_tasks / rm.failed_tasks)
        else:
            recovery = 1.0  # No failures = perfect recovery.
        scores["recovery_rate"] = recovery * MAX_SCORE
        weights["recovery_rate"] = cfg.recovery_rate_weight

    if cfg.consistency_enabled:
        if rm.quality_score_stddev is not None:
            val = max(
                0.0,
                MAX_SCORE - rm.quality_score_stddev * cfg.consistency_k,
            )
        else:
            val = NEUTRAL_SCORE
        scores["consistency"] = val
        weights["consistency"] = cfg.consistency_weight

    if cfg.streak_enabled:
        scores["streak"] = min(
            MAX_SCORE,
            rm.current_success_streak * cfg.streak_factor,
        )
        weights["streak"] = cfg.streak_weight

    return scores, weights
