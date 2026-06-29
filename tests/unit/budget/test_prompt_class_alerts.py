"""Per-prompt-purpose cost / latency alert dispatch (epic #2490, scope J1).

Covers the opt-in thresholds on ``PromptClassAlertConfig`` and the
``CallAnalyticsService`` evaluation + dispatch path, including the live trigger:
``get_prompt_class_breakdown`` evaluates the thresholds so the dashboard read
path fires alerts when a purpose's cost or p95 latency crosses its ceiling.
"""

from datetime import UTC, datetime
from typing import Final

import pytest

from synthorg.budget.call_analytics import CallAnalyticsService
from synthorg.budget.call_analytics_config import (
    CallAnalyticsConfig,
    PromptClassAlertConfig,
)
from synthorg.budget.call_analytics_models import (
    PromptClassBreakdown,
    PromptClassBreakdownRow,
)
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.notifications.dispatcher import NotificationDispatcher
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_COST_CEILING: Final[float] = 1.0
_LATENCY_CEILING_MS: Final[float] = 500.0


def _row(*, cost: float, p95: float | None) -> PromptClassBreakdownRow:
    return PromptClassBreakdownRow(
        prompt_class_id=PromptPurposeId.MEMORY_RERANK.value,
        total_cost=cost,
        call_count=1,
        input_tokens=10,
        output_tokens=5,
        p95_latency_ms=p95,
        retry_rate=0.0,
    )


def _service(
    config: CallAnalyticsConfig,
    dispatcher: NotificationDispatcher | None = None,
) -> CallAnalyticsService:
    return CallAnalyticsService(
        cost_tracker=mock_of[CostTrackerProtocol](),
        config=config,
        notification_dispatcher=dispatcher,
    )


async def test_no_thresholds_dispatches_nothing() -> None:
    dispatcher = mock_of[NotificationDispatcher]()
    service = _service(CallAnalyticsConfig(), dispatcher)

    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=99.0, p95=9999.0),))
    )

    assert dispatcher.dispatch.await_count == 0


async def test_cost_over_ceiling_dispatches() -> None:
    dispatcher = mock_of[NotificationDispatcher]()
    config = CallAnalyticsConfig(
        prompt_class_alerts=PromptClassAlertConfig(cost_warn=_COST_CEILING)
    )
    service = _service(config, dispatcher)

    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=_COST_CEILING + 0.5, p95=None),))
    )

    assert dispatcher.dispatch.await_count == 1
    assert "cost" in dispatcher.dispatch.await_args.args[0].title.lower()


async def test_cost_under_ceiling_no_dispatch() -> None:
    dispatcher = mock_of[NotificationDispatcher]()
    config = CallAnalyticsConfig(
        prompt_class_alerts=PromptClassAlertConfig(cost_warn=_COST_CEILING)
    )
    service = _service(config, dispatcher)

    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=_COST_CEILING, p95=None),))
    )

    assert dispatcher.dispatch.await_count == 0


async def test_p95_latency_over_ceiling_dispatches() -> None:
    dispatcher = mock_of[NotificationDispatcher]()
    config = CallAnalyticsConfig(
        prompt_class_alerts=PromptClassAlertConfig(
            p95_latency_warn_ms=_LATENCY_CEILING_MS
        )
    )
    service = _service(config, dispatcher)

    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=0.0, p95=_LATENCY_CEILING_MS + 100.0),))
    )

    assert dispatcher.dispatch.await_count == 1
    assert "latency" in dispatcher.dispatch.await_args.args[0].title.lower()


async def test_latency_threshold_ignores_rows_without_latency() -> None:
    dispatcher = mock_of[NotificationDispatcher]()
    config = CallAnalyticsConfig(
        prompt_class_alerts=PromptClassAlertConfig(
            p95_latency_warn_ms=_LATENCY_CEILING_MS
        )
    )
    service = _service(config, dispatcher)

    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=0.0, p95=None),))
    )

    assert dispatcher.dispatch.await_count == 0


async def test_disabled_config_dispatches_nothing() -> None:
    dispatcher = mock_of[NotificationDispatcher]()
    config = CallAnalyticsConfig(
        enabled=False,
        prompt_class_alerts=PromptClassAlertConfig(cost_warn=_COST_CEILING),
    )
    service = _service(config, dispatcher)

    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=_COST_CEILING + 5.0, p95=None),))
    )

    assert dispatcher.dispatch.await_count == 0


async def test_no_dispatcher_runs_without_error() -> None:
    config = CallAnalyticsConfig(
        prompt_class_alerts=PromptClassAlertConfig(cost_warn=_COST_CEILING)
    )
    service = _service(config, dispatcher=None)

    # Threshold crossed but no dispatcher wired: logs only, never raises.
    await service.check_prompt_class_alerts(
        PromptClassBreakdown(rows=(_row(cost=_COST_CEILING + 1.0, p95=None),))
    )


async def test_breakdown_read_path_fires_alert() -> None:
    # The live wiring: reading the by-purpose breakdown evaluates the
    # thresholds, so a configured deployment alerts off the dashboard's call.
    dispatcher = mock_of[NotificationDispatcher]()
    tracker = CostTracker()
    await tracker.record(
        CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test-provider",
            model="example-small-001",
            input_tokens=10,
            output_tokens=5,
            cost=_COST_CEILING + 2.0,
            currency="EUR",
            timestamp=datetime(2026, 5, 1, 12, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
            prompt_class_id=PromptPurposeId.MEMORY_RERANK,
        )
    )
    config = CallAnalyticsConfig(
        prompt_class_alerts=PromptClassAlertConfig(cost_warn=_COST_CEILING)
    )
    service = CallAnalyticsService(
        cost_tracker=tracker, config=config, notification_dispatcher=dispatcher
    )

    await service.get_prompt_class_breakdown()

    assert dispatcher.dispatch.await_count == 1
