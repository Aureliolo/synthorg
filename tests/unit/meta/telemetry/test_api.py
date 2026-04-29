"""Unit tests for the cross-deployment analytics API controller."""

from collections.abc import Generator

import pytest

from synthorg.api.controllers import meta_analytics
from synthorg.api.controllers.meta_analytics import (
    _require_collector,
    configure_analytics_controller,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.telemetry.collector import InMemoryAnalyticsCollector
from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent, EventBatch
from synthorg.meta.telemetry.recommender import DefaultThresholdRecommender

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_analytics_controller() -> Generator[None]:
    """Reset module-level globals after each test."""
    yield
    configure_analytics_controller(None, None)


def _make_event(
    deployment_id: str = "d-0",
    source_rule: str = "coordination_overhead",
    decision: str | None = "approved",
    event_type: str = "proposal_decision",
) -> AnonymizedOutcomeEvent:
    return AnonymizedOutcomeEvent(
        deployment_id=NotBlankStr(deployment_id),
        event_type=event_type,  # type: ignore[arg-type]
        timestamp=NotBlankStr("2026-04-16"),
        altitude=NotBlankStr("config_tuning"),
        source_rule=NotBlankStr(source_rule),
        decision=decision,  # type: ignore[arg-type]
        confidence=0.8,
        enabled_altitudes=(NotBlankStr("config_tuning"),),
        sdk_version=NotBlankStr("0.6.8"),
    )


class TestRequireCollector:
    """Tests for _require_collector()."""

    def test_raises_when_collector_not_configured(self) -> None:
        configure_analytics_controller(None, None)
        with pytest.raises(ServiceUnavailableError):
            _require_collector()

    def test_returns_collector_when_configured(self) -> None:
        collector = InMemoryAnalyticsCollector()
        configure_analytics_controller(collector, DefaultThresholdRecommender())
        result = _require_collector()
        assert result is collector


class TestConfigureAnalyticsController:
    """Tests for configure_analytics_controller()."""

    def test_sets_min_deployments_floor(self) -> None:
        collector = InMemoryAnalyticsCollector()
        configure_analytics_controller(
            collector,
            DefaultThresholdRecommender(),
            min_deployments_floor=7,
        )
        assert meta_analytics._min_deployments_floor == 7

    def test_default_floor_is_three(self) -> None:
        configure_analytics_controller(None, None)
        assert meta_analytics._min_deployments_floor == 3


class TestControllerDataFlow:
    """Tests for the wired collector/recommender data flow."""

    async def test_ingest_and_query_patterns(self) -> None:
        collector = InMemoryAnalyticsCollector()
        configure_analytics_controller(collector, DefaultThresholdRecommender())

        events = tuple(_make_event(deployment_id=f"d-{i}") for i in range(5))
        batch = EventBatch(events=events)
        count = await collector.ingest(batch.events)
        assert count == 5
        assert collector.event_count == 5

        patterns = await collector.query_patterns(min_deployments=3)
        assert len(patterns) == 1
        assert patterns[0].deployment_count == 5

    async def test_min_deployments_floor_clamps_queries(self) -> None:
        """Config floor prevents callers from lowering min_deployments."""
        collector = InMemoryAnalyticsCollector()
        configure_analytics_controller(
            collector,
            DefaultThresholdRecommender(),
            min_deployments_floor=5,
        )

        events = tuple(_make_event(deployment_id=f"d-{i}") for i in range(3))
        await collector.ingest(events)

        # Even requesting min_deployments=1, floor clamps to 5.
        effective = max(1, meta_analytics._min_deployments_floor)
        patterns = await collector.query_patterns(min_deployments=effective)
        assert len(patterns) == 0  # 3 unique deployments < floor of 5

    async def test_ingest_and_get_recommendations(self) -> None:
        collector = InMemoryAnalyticsCollector()
        recommender = DefaultThresholdRecommender()
        configure_analytics_controller(collector, recommender)

        events: list[AnonymizedOutcomeEvent] = []
        for i in range(5):
            events.append(
                _make_event(deployment_id=f"d-{i}", decision="approved"),
            )
            rollout = AnonymizedOutcomeEvent(
                deployment_id=NotBlankStr(f"d-{i}"),
                event_type="rollout_result",
                timestamp=NotBlankStr("2026-04-16"),
                altitude=NotBlankStr("config_tuning"),
                source_rule=NotBlankStr("coordination_overhead"),
                rollout_outcome=NotBlankStr("success"),
                observation_hours=48.0,
                enabled_altitudes=(NotBlankStr("config_tuning"),),
                sdk_version=NotBlankStr("0.6.8"),
            )
            events.append(rollout)
        await collector.ingest(tuple(events))

        recs = await recommender.get_recommendations(collector=collector)
        assert len(recs) >= 1
        assert recs[0].rule_name == "coordination_overhead"
