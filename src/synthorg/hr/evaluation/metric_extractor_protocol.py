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

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.models import EvaluationContext

# Read-only sentinels used as default factories so the type-level
# `Mapping` (read-only) promise matches the runtime value. These are
# shared across instances; safe because ``MappingProxyType`` is a
# read-only view over the underlying empty dict.
_EMPTY_FLOAT_MAP = MappingProxyType[str, float]({})
_EMPTY_LOG_KWARGS = MappingProxyType[str, str | int | float | bool]({})


@dataclass(frozen=True, slots=True)
class ExtractedMetrics:
    """Output of ``MetricExtractor.extract()``.

    Attributes:
        scores: Sub-metric name -> 0.0-10.0 score. ``ConfigurablePillarScorer``
            applies ``redistribute_weights`` then computes a weighted
            average across ``scores`` keyed by ``weights``. Every key
            in ``weights`` must be present in ``scores``; the
            ``__post_init__`` validator enforces this.
        weights: Sub-metric name -> raw weight. Disabled metrics
            should be omitted entirely (not present here at all).
            The composite normalises with ``redistribute_weights``.
        data_points: Number of underlying observations the extractor
            saw. Drives the base confidence ``min(1.0, data_points / N)``.
        confidence_multiplier: Multiplied into the base confidence
            (after the ``data_points / N`` normalisation). Must be
            non-negative. Default ``1.0`` (no penalty/bonus).
            Intelligence uses this for the calibration-drift penalty;
            Experience uses it to redirect the confidence saturation
            point from ``FULL_CONFIDENCE_DATA_POINTS`` to
            ``min_feedback_count * 3``.
        insufficient_data: When ``True`` the composite short-circuits
            to a neutral ``PillarScore`` (score = ``NEUTRAL_SCORE``,
            confidence = 0.0) and emits the
            ``insufficient_data_event`` log.
        insufficient_data_event_kwargs: Extra structured kwargs for
            the ``EVAL_PILLAR_INSUFFICIENT_DATA`` log event when
            ``insufficient_data`` is ``True`` (typically
            ``{"reason": "<short_id>"}``). Value type is restricted
            to JSON-serialisable scalars so structured-log sinks never
            choke on the payload.
        neutral_data_point_count: When ``insufficient_data`` is
            ``True`` and this is non-``None``, overrides the neutral
            ``PillarScore.data_point_count`` (default ``0``).
            Experience uses this so the "not enough feedback yet"
            neutral score still carries the actual count.
    """

    scores: Mapping[str, float] = field(default_factory=lambda: _EMPTY_FLOAT_MAP)
    weights: Mapping[str, float] = field(default_factory=lambda: _EMPTY_FLOAT_MAP)
    data_points: int = 0
    confidence_multiplier: float = 1.0
    insufficient_data: bool = False
    insufficient_data_event_kwargs: Mapping[str, str | int | float | bool] = field(
        default_factory=lambda: _EMPTY_LOG_KWARGS,
    )
    neutral_data_point_count: int | None = None

    def __post_init__(self) -> None:
        """Enforce ``ExtractedMetrics`` invariants at construction.

        - ``confidence_multiplier`` must be non-negative.
        - When ``insufficient_data`` is ``False``, every key in
          ``weights`` must have a corresponding entry in ``scores``;
          otherwise ``ConfigurablePillarScorer`` would ``KeyError`` at
          runtime building the weighted sum.
        - The three caller-supplied mappings (``scores``, ``weights``,
          ``insufficient_data_event_kwargs``) are deep-copied and
          wrapped in ``MappingProxyType`` so a later mutation of the
          originating dict by the extractor cannot bypass these
          checks or change scorer inputs after validation.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.confidence_multiplier < 0.0:
            msg = (
                f"confidence_multiplier must be non-negative, "
                f"got {self.confidence_multiplier}"
            )
            raise ValueError(msg)
        if not self.insufficient_data:
            if not self.weights:
                msg = (
                    "weights must be non-empty when insufficient_data is False; "
                    "ConfigurablePillarScorer.redistribute_weights would raise. "
                    "Set insufficient_data=True (with insufficient_data_event_kwargs) "
                    "to short-circuit to a neutral PillarScore instead."
                )
                raise ValueError(msg)
            missing = set(self.weights) - set(self.scores)
            if missing:
                msg = (
                    f"weights keys {sorted(missing)} have no matching "
                    f"scores entries; ConfigurablePillarScorer would KeyError"
                )
                raise ValueError(msg)
            extra = set(self.scores) - set(self.weights)
            if extra:
                msg = (
                    f"scores keys {sorted(extra)} have no matching "
                    f"weights entries; they would surface in "
                    f"PillarScore.breakdown without contributing to the "
                    f"weighted score. Drop them from scores or supply a "
                    f"matching weight"
                )
                raise ValueError(msg)
        # Freeze the mappings against post-construction mutation.
        # ``frozen=True`` only prevents attribute reassignment; the
        # underlying dicts can still be mutated through the caller's
        # original reference. Skip the wrap when the value is already
        # one of our read-only sentinels (default case) to avoid an
        # unnecessary deepcopy round-trip.
        if self.scores is not _EMPTY_FLOAT_MAP:
            object.__setattr__(
                self,
                "scores",
                MappingProxyType(deepcopy(dict(self.scores))),
            )
        if self.weights is not _EMPTY_FLOAT_MAP:
            object.__setattr__(
                self,
                "weights",
                MappingProxyType(deepcopy(dict(self.weights))),
            )
        if self.insufficient_data_event_kwargs is not _EMPTY_LOG_KWARGS:
            object.__setattr__(
                self,
                "insufficient_data_event_kwargs",
                MappingProxyType(deepcopy(dict(self.insufficient_data_event_kwargs))),
            )


@runtime_checkable
class MetricExtractor(Protocol):
    """Per-pillar sub-metric extraction.

    Implementations read whatever fields they need from the
    ``EvaluationContext`` (snapshot, task records, calibration
    records, feedback, audit counts, trust level, ...) and return
    an ``ExtractedMetrics``. The composite scorer handles the rest.

    ``extract`` is async because some extractors (notably Efficiency)
    need to await ``ConfigResolver`` calls for dynamic configuration.
    CPU-bound extractors simply ``return`` directly.
    """

    @property
    def pillar(self) -> EvaluationPillar:
        """Which pillar this extractor produces metrics for."""
        ...

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        """Read the relevant context fields and return sub-metric scores.

        Returns:
            Result of type ``ExtractedMetrics``.
        """
        ...
