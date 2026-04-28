"""Unit tests for ``ConfigurablePillarScorer`` and ``ExtractedMetrics``.

The tests stub a fake extractor so they exercise the composite
finalize step (weighted average, clamp, confidence multiplier,
weight redistribution, neutral fallback) in isolation. Per-pillar
extractor tests live in ``test_*_extractor.py``.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from synthorg.hr.evaluation.configurable_scorer import ConfigurablePillarScorer
from synthorg.hr.evaluation.constants import (
    FULL_CONFIDENCE_DATA_POINTS,
    NEUTRAL_SCORE,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.metric_extractor_protocol import (
    ExtractedMetrics,
    MetricExtractor,
)
from synthorg.hr.evaluation.models import EvaluationContext

from .conftest import make_evaluation_context

pytestmark = pytest.mark.unit


class _FakeExtractor:
    """Stub MetricExtractor for isolated composite testing."""

    def __init__(  # noqa: PLR0913 -- test stub; explicit kwargs are clearer than a config object
        self,
        *,
        pillar: EvaluationPillar = EvaluationPillar.RESILIENCE,
        scores: Mapping[str, float] | None = None,
        weights: Mapping[str, float] | None = None,
        data_points: int = 0,
        confidence_multiplier: float = 1.0,
        insufficient_data: bool | None = None,
        insufficient_data_event_kwargs: Mapping[str, str] | None = None,
    ) -> None:
        self._pillar = pillar
        # When the caller does not specify insufficient_data, infer
        # from the (scores, weights) shape: an empty pair signals
        # "nothing to score" and matches ExtractedMetrics's
        # insufficient_data invariant.
        effective_insufficient = (
            insufficient_data
            if insufficient_data is not None
            else not (scores and weights)
        )
        self._metrics = ExtractedMetrics(
            scores=scores or {},
            weights=weights or {},
            data_points=data_points,
            confidence_multiplier=confidence_multiplier,
            insufficient_data=effective_insufficient,
            insufficient_data_event_kwargs=insufficient_data_event_kwargs or {},
        )

    @property
    def pillar(self) -> EvaluationPillar:
        return self._pillar

    async def extract(self, context: EvaluationContext) -> ExtractedMetrics:
        del context
        return self._metrics


class TestExtractedMetrics:
    """``ExtractedMetrics`` invariants."""

    def test_defaults_require_insufficient_data(self) -> None:
        # An empty weights map only makes semantic sense when the
        # extractor is signalling insufficient_data; otherwise the
        # composite would feed an empty mapping into
        # ``redistribute_weights`` which raises.
        m = ExtractedMetrics(insufficient_data=True)
        assert m.scores == {}
        assert m.weights == {}
        assert m.data_points == 0
        assert m.confidence_multiplier == 1.0
        assert m.insufficient_data is True
        assert m.insufficient_data_event_kwargs == {}

    def test_default_no_arg_construction_rejected(self) -> None:
        # Default-constructed ExtractedMetrics() (insufficient_data=False
        # + empty weights) is an invalid combination.
        with pytest.raises(ValueError, match="weights must be non-empty"):
            ExtractedMetrics()

    def test_is_frozen(self) -> None:
        m = ExtractedMetrics(insufficient_data=True, data_points=5)
        with pytest.raises(AttributeError):
            m.data_points = 10  # type: ignore[misc]


class TestExtractedMetricsValidation:
    """``ExtractedMetrics.__post_init__`` invariants."""

    def test_negative_confidence_multiplier_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            ExtractedMetrics(confidence_multiplier=-0.5)

    def test_weights_with_missing_scores_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="have no matching"):
            ExtractedMetrics(
                scores={"a": 5.0},
                weights={"a": 0.5, "b": 0.5},
                data_points=10,
            )

    def test_misalignment_allowed_when_insufficient_data(self) -> None:
        # When the extractor short-circuits, weights/scores alignment
        # is not required (the composite ignores them).
        result = ExtractedMetrics(
            insufficient_data=True,
            weights={"missing": 0.5},
            scores={},
        )
        assert result.insufficient_data is True

    def test_caller_dict_mutation_does_not_leak(self) -> None:
        # The Mapping fields should be deep-copied + frozen so a
        # post-construction mutation of the caller's dict cannot
        # change the dataclass's view.
        scores_in = {"a": 5.0}
        weights_in = {"a": 1.0}
        kwargs_in = {"reason": "x"}
        m = ExtractedMetrics(
            scores=scores_in,
            weights=weights_in,
            data_points=1,
            insufficient_data_event_kwargs=kwargs_in,
        )
        scores_in["a"] = 999.0
        weights_in["b"] = 999.0
        kwargs_in["reason"] = "mutated"
        assert m.scores == {"a": 5.0}
        assert m.weights == {"a": 1.0}
        assert m.insufficient_data_event_kwargs == {"reason": "x"}


class TestConfigurablePillarScorer:
    """``ConfigurablePillarScorer`` finalize behaviour."""

    def test_implements_protocol(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(pillar=EvaluationPillar.RESILIENCE),
        )
        # Both required Protocol attrs must exist.
        assert scorer.pillar is EvaluationPillar.RESILIENCE
        assert scorer.name.startswith("configurable[")

    def test_pillar_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="does not match"):
            ConfigurablePillarScorer(
                EvaluationPillar.RESILIENCE,
                _FakeExtractor(pillar=EvaluationPillar.GOVERNANCE),
            )

    async def test_insufficient_data_returns_neutral(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                insufficient_data=True,
                insufficient_data_event_kwargs={"reason": "no_input"},
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        assert result.score == NEUTRAL_SCORE
        assert result.confidence == 0.0
        assert result.data_point_count == 0
        assert result.pillar is EvaluationPillar.RESILIENCE

    async def test_weighted_average(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 8.0, "b": 4.0},
                weights={"a": 0.5, "b": 0.5},
                data_points=FULL_CONFIDENCE_DATA_POINTS,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        # 8*0.5 + 4*0.5 = 6.0
        assert result.score == 6.0
        assert result.confidence == 1.0

    async def test_weight_redistribution(self) -> None:
        # Raw weights sum to 0.4 + 0.6 = 1.0 already, but unequal.
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 10.0, "b": 0.0},
                weights={"a": 0.4, "b": 0.6},
                data_points=10,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        # 10*0.4 + 0*0.6 = 4.0
        assert result.score == 4.0

    async def test_uneven_raw_weights_normalize(self) -> None:
        # Raw weights sum to 4.0; should normalize to 0.25/0.75.
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 8.0, "b": 4.0},
                weights={"a": 1.0, "b": 3.0},
                data_points=10,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        # 8*0.25 + 4*0.75 = 5.0
        assert result.score == 5.0

    async def test_score_clamped_to_max(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 999.0},
                weights={"a": 1.0},
                data_points=10,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        assert result.score == 10.0  # MAX_SCORE

    async def test_confidence_scales_with_data_points(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 5.0},
                weights={"a": 1.0},
                data_points=FULL_CONFIDENCE_DATA_POINTS // 2,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        assert result.confidence == 0.5

    async def test_confidence_saturates_at_one(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 5.0},
                weights={"a": 1.0},
                data_points=FULL_CONFIDENCE_DATA_POINTS * 5,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        assert result.confidence == 1.0

    async def test_confidence_multiplier_reduces(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.INTELLIGENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.INTELLIGENCE,
                scores={"a": 5.0},
                weights={"a": 1.0},
                data_points=FULL_CONFIDENCE_DATA_POINTS,
                confidence_multiplier=0.4,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        # base = 1.0, multiplier = 0.4 -> 0.4
        assert result.confidence == 0.4

    async def test_breakdown_sorted_by_metric_name(self) -> None:
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"zebra": 3.0, "apple": 7.0},
                weights={"zebra": 0.5, "apple": 0.5},
                data_points=10,
            ),
        )
        result = await scorer.score(context=make_evaluation_context())
        names = [name for name, _ in result.breakdown]
        assert names == ["apple", "zebra"]

    async def test_evaluated_at_uses_context_now(self) -> None:
        when = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            _FakeExtractor(
                pillar=EvaluationPillar.RESILIENCE,
                scores={"a": 5.0},
                weights={"a": 1.0},
                data_points=10,
            ),
        )
        result = await scorer.score(context=make_evaluation_context(now=when))
        assert result.evaluated_at == when

    def test_metric_extractor_protocol_runtime_check(self) -> None:
        # FakeExtractor should satisfy the MetricExtractor Protocol.
        assert isinstance(
            _FakeExtractor(pillar=EvaluationPillar.RESILIENCE),
            MetricExtractor,
        )
