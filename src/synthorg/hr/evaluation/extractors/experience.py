"""User Experience pillar metric extractor.

Lifts the 5 feedback rating dimensions (clarity, tone, helpfulness,
trust, satisfaction) from ``EvaluationContext.feedback``. Composed
with ``ConfigurablePillarScorer`` to produce the experience
``PillarScoringStrategy``.

Custom confidence multiplier: this pillar's confidence saturates at
``min_feedback_count * _FULL_CONFIDENCE_FEEDBACK_MULTIPLIER`` data
points instead of the default ``FULL_CONFIDENCE_DATA_POINTS = 10``.
We express that as a multiplier on the standard
``data_points / FULL_CONFIDENCE_DATA_POINTS`` so the composite
finalize step needs no special-casing.
"""

from synthorg.hr.evaluation.config import ExperienceConfig
from synthorg.hr.evaluation.constants import (
    FULL_CONFIDENCE_DATA_POINTS,
    MAX_SCORE,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.extractors._shared import log_disabled_metrics
from synthorg.hr.evaluation.metric_extractor_protocol import ExtractedMetrics
from synthorg.hr.evaluation.models import EvaluationContext, InteractionFeedback

# Full confidence at min_feedback_count * this multiplier.
# Preserved verbatim from FeedbackBasedUxStrategy.
_FULL_CONFIDENCE_FEEDBACK_MULTIPLIER: int = 3

# Rating field accessors keyed by metric name.
_RATING_FIELDS: dict[str, str] = {
    "clarity": "clarity_rating",
    "tone": "tone_rating",
    "helpfulness": "helpfulness_rating",
    "trust": "trust_rating",
    "satisfaction": "satisfaction_rating",
}


def _avg_rating(
    feedback: tuple[InteractionFeedback, ...],
    field: str,
) -> float | None:
    """Average a feedback rating field, ignoring ``None`` values.

    Returns:
        The resulting ``float``, or ``None`` when unavailable.
    """
    vals: list[float] = [
        getattr(fb, field) for fb in feedback if getattr(fb, field) is not None
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)


class ExperienceMetricExtractor:
    """Extract clarity/tone/helpfulness/trust/satisfaction sub-metrics."""

    __slots__ = ()

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        return EvaluationPillar.EXPERIENCE

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Read interaction feedback and emit sub-metric scores.

        Returns:
            Result of type ``ExtractedMetrics``.
        """
        cfg = context.config.experience
        feedback = context.feedback

        if len(feedback) < cfg.min_feedback_count:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={
                    "reason": "insufficient_feedback",
                    "count": len(feedback),
                    "min_required": cfg.min_feedback_count,
                },
                neutral_data_point_count=len(feedback),
            )

        # Audit-trail: emit DEBUG for any rating dimension the
        # operator explicitly disabled via config.
        disabled_metrics = tuple(
            metric
            for metric, enabled in (
                ("clarity", cfg.clarity_enabled),
                ("tone", cfg.tone_enabled),
                ("helpfulness", cfg.helpfulness_enabled),
                ("trust", cfg.trust_enabled),
                ("satisfaction", cfg.satisfaction_enabled),
            )
            if not enabled
        )
        if disabled_metrics:
            log_disabled_metrics(context, EvaluationPillar.EXPERIENCE, disabled_metrics)

        scores, weights = _collect_metrics(cfg, feedback)

        if not weights:
            return ExtractedMetrics(
                insufficient_data=True,
                insufficient_data_event_kwargs={
                    "reason": "no_enabled_metrics_with_data",
                },
                neutral_data_point_count=len(feedback),
            )

        # ConfigurablePillarScorer computes base_confidence as
        # ``data_points / FULL_CONFIDENCE_DATA_POINTS``. We want the
        # final confidence to be ``len(feedback) /
        # (min_feedback_count * 3)``, capped at 1.0. The multiplier
        # below converts the base ratio to the desired one:
        #   final confidence = (data_points / FULL) * (FULL / (min_count * 3))
        #         = data_points / (min_count * 3)
        # The composite then clamps to [0, 1].
        confidence_multiplier = FULL_CONFIDENCE_DATA_POINTS / (
            cfg.min_feedback_count * _FULL_CONFIDENCE_FEEDBACK_MULTIPLIER
        )

        return ExtractedMetrics(
            scores=scores,
            weights=weights,
            data_points=len(feedback),
            confidence_multiplier=confidence_multiplier,
        )


def _collect_metrics(
    cfg: ExperienceConfig,
    feedback: tuple[InteractionFeedback, ...],
) -> tuple[dict[str, float], dict[str, float]]:
    """Gather enabled rating sub-metrics with their raw weights.

    Returns:
        Tuple ``(dict[str, float], dict[str, float])``.
    """
    metric_defs = [
        ("clarity", cfg.clarity_enabled, cfg.clarity_weight),
        ("tone", cfg.tone_enabled, cfg.tone_weight),
        ("helpfulness", cfg.helpfulness_enabled, cfg.helpfulness_weight),
        ("trust", cfg.trust_enabled, cfg.trust_weight),
        ("satisfaction", cfg.satisfaction_enabled, cfg.satisfaction_weight),
    ]

    scores: dict[str, float] = {}
    weights: dict[str, float] = {}
    for metric_name, enabled, weight in metric_defs:
        if not enabled:
            continue
        avg = _avg_rating(feedback, _RATING_FIELDS[metric_name])
        if avg is not None:
            scores[metric_name] = avg * MAX_SCORE
            weights[metric_name] = weight
    return scores, weights
