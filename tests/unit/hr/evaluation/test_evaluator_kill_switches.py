"""Coverage for the four hr.evaluation_*_enabled flag → pillar gates.

Each flag maps to either a pillar or an efficiency sub-metric and
must drive the corresponding score path when toggled via the
ConfigResolver (best-effort mapping documented in
``EvaluationService.__init__``).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.evaluator import EvaluationService

pytestmark = pytest.mark.unit


def _make_resolver(values: dict[str, bool]) -> Any:
    resolver = AsyncMock()

    async def _get_bool(namespace: str, key: str) -> bool:
        return values.get(f"{namespace}/{key}", True)

    resolver.get_bool = AsyncMock(side_effect=_get_bool)
    return resolver


@pytest.fixture
def tracker_with_no_data() -> Any:
    tracker = MagicMock()
    snapshot = MagicMock()
    snapshot.windows = ()
    tracker.get_snapshot = AsyncMock(return_value=snapshot)
    tracker.get_task_metrics = MagicMock(return_value=())
    tracker.sampler = None
    return tracker


async def test_intelligence_pillar_skipped_when_quality_disabled(
    tracker_with_no_data: Any,
) -> None:
    """Intelligence pillar is omitted when evaluation_quality_enabled=False."""
    resolver = _make_resolver({"hr/evaluation_quality_enabled": False})
    service = EvaluationService(
        tracker=tracker_with_no_data,
        config=EvaluationConfig(),
        config_resolver=resolver,
    )
    enabled, weights = await service._resolve_enabled_pillars("agent-001")
    pillars = {p for p, _strategy in enabled}
    assert EvaluationPillar.INTELLIGENCE not in pillars
    assert "intelligence" not in weights
    # Other pillars present and weights redistributed to sum near 1.0.
    assert EvaluationPillar.RESILIENCE in pillars
    total_weight = sum(weights.values())
    assert 0.99 <= total_weight <= 1.01


async def test_resilience_pillar_skipped_when_task_count_disabled(
    tracker_with_no_data: Any,
) -> None:
    """Resilience pillar is omitted when evaluation_task_count_enabled=False."""
    resolver = _make_resolver({"hr/evaluation_task_count_enabled": False})
    service = EvaluationService(
        tracker=tracker_with_no_data,
        config=EvaluationConfig(),
        config_resolver=resolver,
    )
    enabled, weights = await service._resolve_enabled_pillars("agent-001")
    pillars = {p for p, _strategy in enabled}
    assert EvaluationPillar.RESILIENCE not in pillars
    assert "resilience" not in weights
    # Intelligence still in (default True for quality flag).
    assert EvaluationPillar.INTELLIGENCE in pillars


async def test_efficiency_cost_submetric_gated_by_evaluation_cost_enabled(
    tracker_with_no_data: Any,
) -> None:
    """Cost sub-metric drops out when evaluation_cost_enabled=False."""
    from synthorg.hr.evaluation.extractors.efficiency import (
        EfficiencyMetricExtractor,
    )

    resolver = _make_resolver({"hr/evaluation_cost_enabled": False})
    extractor = EfficiencyMetricExtractor(config_resolver=resolver)
    context = _make_efficiency_context()
    metrics = await extractor.extract(context)
    assert "cost" not in metrics.weights
    assert "time" in metrics.weights
    assert "tokens" in metrics.weights


async def test_efficiency_time_submetric_gated_by_evaluation_latency_enabled(
    tracker_with_no_data: Any,
) -> None:
    """Time sub-metric drops out when evaluation_latency_enabled=False."""
    from synthorg.hr.evaluation.extractors.efficiency import (
        EfficiencyMetricExtractor,
    )

    resolver = _make_resolver({"hr/evaluation_latency_enabled": False})
    extractor = EfficiencyMetricExtractor(config_resolver=resolver)
    context = _make_efficiency_context()
    metrics = await extractor.extract(context)
    assert "time" not in metrics.weights
    assert "cost" in metrics.weights
    assert "tokens" in metrics.weights


def _make_efficiency_context() -> Any:
    """Build a minimal EvaluationContext for extractor unit tests.

    The window has every sub-metric populated so we observe which
    ones the extractor drops based on the resolver's kill switches.
    """
    from synthorg.hr.performance.models import WindowMetrics
    from tests.unit.hr.evaluation.conftest import (
        make_evaluation_context,
        make_snapshot,
    )

    window = WindowMetrics(
        window_size="30d",
        data_point_count=10,
        tasks_completed=10,
        tasks_failed=0,
        avg_cost_per_task=0.5,
        currency=DEFAULT_CURRENCY,
        avg_completion_time_seconds=120.0,
        avg_tokens_per_task=1000,
    )
    snapshot = make_snapshot(agent_id="agent-001", windows=(window,))
    base = make_evaluation_context()
    return base.model_copy(update={"snapshot": snapshot})
