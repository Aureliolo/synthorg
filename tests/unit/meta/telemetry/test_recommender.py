"""Unit tests for the threshold recommender."""

from types import MappingProxyType

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.telemetry.collector import InMemoryAnalyticsCollector
from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent
from synthorg.meta.telemetry.recommender import (
    _BUDGET_OVERRUN_DAYS_DEFAULT,
    _COORDINATION_RATIO_DEFAULT,
    _ERROR_FINDINGS_DEFAULT,
    _OVERHEAD_PCT_DEFAULT,
    _QUALITY_DROP_DEFAULT,
    _REDUNDANCY_RATE_DEFAULT,
    _RULE_THRESHOLD_MAP,
    _STRAGGLER_GAP_RATIO_DEFAULT,
    _SUCCESS_RATE_DEFAULT,
    DefaultThresholdRecommender,
)

pytestmark = pytest.mark.unit


def _make_event(
    *,
    deployment_id: str = "d-0",
    source_rule: str = "coordination_overhead",
    decision: str | None = "approved",
    rollout_outcome: str | None = None,
    event_type: str = "proposal_decision",
    confidence: float | None = 0.8,
) -> AnonymizedOutcomeEvent:
    return AnonymizedOutcomeEvent(
        deployment_id=NotBlankStr(deployment_id),
        event_type=event_type,  # type: ignore[arg-type]
        timestamp=NotBlankStr("2026-04-16"),
        altitude=NotBlankStr("config_tuning"),
        source_rule=NotBlankStr(source_rule),
        decision=decision,  # type: ignore[arg-type]
        confidence=confidence,
        rollout_outcome=NotBlankStr(rollout_outcome) if rollout_outcome else None,
        enabled_altitudes=(NotBlankStr("config_tuning"),),
        sdk_version=NotBlankStr("0.6.8"),
    )


class TestDefaultThresholdRecommender:
    """Tests for DefaultThresholdRecommender."""

    async def test_empty_collector_no_recommendations(self) -> None:
        collector = InMemoryAnalyticsCollector()
        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        assert recs == ()

    async def test_high_approval_high_success_recommends_relaxing(self) -> None:
        collector = InMemoryAnalyticsCollector()
        # 5 deployments, all approved decisions + successful rollouts.
        events: list[AnonymizedOutcomeEvent] = []
        for i in range(5):
            events.append(
                _make_event(deployment_id=f"d-{i}", decision="approved"),
            )
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    event_type="rollout_result",
                    decision=None,
                    confidence=None,
                    rollout_outcome="success",
                ),
            )
        await collector.ingest(tuple(events))

        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        assert len(recs) == 1
        rec = recs[0]
        assert rec.rule_name == "coordination_overhead"
        assert "too conservative" in rec.rationale
        assert rec.recommended_value > rec.current_default

    async def test_low_approval_recommends_tightening(self) -> None:
        collector = InMemoryAnalyticsCollector()
        # 5 deployments, mostly rejected decisions.
        events: list[AnonymizedOutcomeEvent] = []
        for i in range(5):
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    decision="rejected",
                    confidence=0.3,
                ),
            )
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    decision="rejected",
                    confidence=0.3,
                ),
            )
        await collector.ingest(tuple(events))

        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        assert len(recs) == 1
        rec = recs[0]
        assert "too aggressive" in rec.rationale
        assert rec.recommended_value < rec.current_default

    async def test_moderate_approval_no_recommendation(self) -> None:
        collector = InMemoryAnalyticsCollector()
        # 5 deployments, ~50% approval, ~50% success.
        events: list[AnonymizedOutcomeEvent] = []
        for i in range(5):
            events.append(
                _make_event(deployment_id=f"d-{i}", decision="approved"),
            )
            events.append(
                _make_event(deployment_id=f"d-{i}", decision="rejected"),
            )
        await collector.ingest(tuple(events))

        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        assert recs == ()

    async def test_unknown_rule_skipped(self) -> None:
        collector = InMemoryAnalyticsCollector()
        events = tuple(
            _make_event(
                deployment_id=f"d-{i}",
                source_rule="custom",
                decision="approved",
            )
            for i in range(5)
        )
        # Add rollout successes too.
        rollouts = tuple(
            _make_event(
                deployment_id=f"d-{i}",
                source_rule="custom",
                event_type="rollout_result",
                decision=None,
                confidence=None,
                rollout_outcome="success",
            )
            for i in range(5)
        )
        await collector.ingest(events + rollouts)

        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        # "custom" is not in the threshold map.
        assert recs == ()

    async def test_insufficient_observations_no_recommendation(self) -> None:
        collector = InMemoryAnalyticsCollector()
        # Only 3 events total across 3 deployments.
        events = tuple(
            _make_event(deployment_id=f"d-{i}", decision="approved") for i in range(3)
        )
        await collector.ingest(events)

        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        # 3 events < min 10 observations.
        assert recs == ()

    async def test_recommendations_sorted_by_confidence(self) -> None:
        collector = InMemoryAnalyticsCollector()
        events: list[AnonymizedOutcomeEvent] = []
        # Pattern 1: coordination_overhead, 5 deployments, high confidence.
        for i in range(5):
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    source_rule="coordination_overhead",
                    decision="approved",
                    confidence=0.9,
                ),
            )
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    source_rule="coordination_overhead",
                    event_type="rollout_result",
                    decision=None,
                    confidence=None,
                    rollout_outcome="success",
                ),
            )
        # Pattern 2: quality_declining, 5 deployments, lower confidence.
        for i in range(5):
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    source_rule="quality_declining",
                    decision="approved",
                    confidence=0.5,
                ),
            )
            events.append(
                _make_event(
                    deployment_id=f"d-{i}",
                    source_rule="quality_declining",
                    event_type="rollout_result",
                    decision=None,
                    confidence=None,
                    rollout_outcome="success",
                ),
            )
        await collector.ingest(tuple(events))

        recommender = DefaultThresholdRecommender()
        recs = await recommender.get_recommendations(collector=collector)
        assert len(recs) == 2
        # First recommendation should have higher confidence.
        assert recs[0].confidence >= recs[1].confidence


class TestRuleThresholdMapStructure:
    """Invariants on ``_RULE_THRESHOLD_MAP`` (HYG-2 extraction)."""

    def test_map_is_immutable(self) -> None:
        assert isinstance(_RULE_THRESHOLD_MAP, MappingProxyType)
        with pytest.raises(TypeError):
            _RULE_THRESHOLD_MAP["new_rule"] = (  # type: ignore[index]
                "field",
                1.0,
            )

    @pytest.mark.parametrize(
        ("rule", "expected_default"),
        [
            ("quality_declining", _QUALITY_DROP_DEFAULT),
            ("success_rate_drop", _SUCCESS_RATE_DEFAULT),
            ("budget_overrun", _BUDGET_OVERRUN_DAYS_DEFAULT),
            ("coordination_cost_ratio", _COORDINATION_RATIO_DEFAULT),
            ("coordination_overhead", _OVERHEAD_PCT_DEFAULT),
            ("straggler_bottleneck", _STRAGGLER_GAP_RATIO_DEFAULT),
            ("redundancy", _REDUNDANCY_RATE_DEFAULT),
            ("error_spike", _ERROR_FINDINGS_DEFAULT),
        ],
    )
    def test_map_values_reference_named_constants(
        self,
        rule: str,
        expected_default: float,
    ) -> None:
        # Value equality ties the map to the module-level Final constants
        # so re-tuning a named constant automatically updates the map.
        # A value check is more robust than ``is`` because CPython's
        # float-interning behaviour for small floats is not guaranteed
        # by the language spec.
        _, actual_default = _RULE_THRESHOLD_MAP[rule]
        assert actual_default == expected_default
