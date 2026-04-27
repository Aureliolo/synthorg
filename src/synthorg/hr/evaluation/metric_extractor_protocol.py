"""Per-pillar metric extractor protocol.

The pillar strategies all share the same finalize skeleton (collect
sub-metrics with weights, redistribute, weighted-average, clamp,
log, return ``PillarScore``). They differ only in *which* sub-metrics
they pull from the ``EvaluationContext`` and how they compute custom
confidence multipliers (e.g. Intelligence calibration drift).

A ``MetricExtractor`` owns the per-pillar extraction logic.
``ConfigurablePillarScorer`` (in ``configurable_scorer``) composes
an extractor with the shared finalize step to produce a
``PillarScoringStrategy``.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.hr.evaluation.enums import EvaluationPillar
    from synthorg.hr.evaluation.models import EvaluationContext


@dataclass(frozen=True, slots=True)
class ExtractedMetrics:
    """Output of ``MetricExtractor.extract()``.

    Attributes:
        scores: Sub-metric name -> 0.0-10.0 score. ``ConfigurablePillarScorer``
            applies ``redistribute_weights`` then computes a weighted
            average across the ``scores.keys()`` overlap with
            ``weights.keys()``.
        weights: Sub-metric name -> raw weight. Disabled metrics
            should be omitted entirely (not present here at all).
            The composite normalises with ``redistribute_weights``.
        data_points: Number of underlying observations the extractor
            saw. Drives the base confidence ``min(1.0, data_points / N)``.
        confidence_multiplier: Multiplied into the base confidence
            (after the ``data_points / N`` normalisation). Default
            ``1.0`` (no penalty/bonus). Intelligence uses this for
            the calibration-drift penalty; Experience uses it to
            redirect the confidence saturation point from
            ``FULL_CONFIDENCE_DATA_POINTS`` to
            ``min_feedback_count * 3``.
        insufficient_data: When ``True`` the composite short-circuits
            to a neutral ``PillarScore`` (score = ``NEUTRAL_SCORE``,
            confidence = 0.0) and emits the
            ``insufficient_data_event`` log.
        insufficient_data_event_kwargs: Extra structured kwargs for
            the ``EVAL_PILLAR_INSUFFICIENT_DATA`` log event when
            ``insufficient_data`` is ``True`` (typically
            ``{"reason": "<short_id>"}``; values may be any
            JSON-serialisable scalar).
        neutral_data_point_count: When ``insufficient_data`` is
            ``True`` and this is non-``None``, overrides the neutral
            ``PillarScore.data_point_count`` (default ``0``).
            Experience uses this so the "not enough feedback yet"
            neutral score still carries the actual count.
    """

    scores: Mapping[str, float] = field(default_factory=dict)
    weights: Mapping[str, float] = field(default_factory=dict)
    data_points: int = 0
    confidence_multiplier: float = 1.0
    insufficient_data: bool = False
    insufficient_data_event_kwargs: Mapping[str, object] = field(default_factory=dict)
    neutral_data_point_count: int | None = None


@runtime_checkable
class MetricExtractor(Protocol):
    """Per-pillar sub-metric extraction.

    Implementations read whatever fields they need from the
    ``EvaluationContext`` (snapshot, task records, calibration
    records, feedback, audit counts, trust level, ...) and return
    an ``ExtractedMetrics``. The composite scorer handles the rest.

    ``extract`` is async because some extractors (notably
    Efficiency, after rebase against the kill-switch resolver
    landed in #1648) need to await ``ConfigResolver`` calls. CPU-bound
    extractors simply ``return`` directly.
    """

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        ...

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Read the relevant context fields and return sub-metric scores."""
        ...
