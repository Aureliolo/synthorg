"""Unit tests for ScalingSignalAggregator."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import structlog

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import (
    ScalingActionType,
    ScalingOutcome,
    ScalingStrategyName,
)
from synthorg.hr.scaling.models import ScalingActionRecord, ScalingDecision
from synthorg.hr.scaling.service import ScalingService
from synthorg.meta.models import OrgScalingSummary
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _window() -> tuple[datetime, datetime]:
    return _NOW - timedelta(days=7), _NOW + timedelta(hours=1)


def _decision(  # noqa: PLR0913
    *,
    decision_id: str = "d-001",
    action_type: ScalingActionType = ScalingActionType.HIRE,
    strategy: ScalingStrategyName = ScalingStrategyName.WORKLOAD,
    rationale: str = "workload up",
    created_at: datetime = _NOW,
    target_role: str | None = "engineer",
    target_agent_id: str | None = None,
) -> ScalingDecision:
    return ScalingDecision(
        id=NotBlankStr(decision_id),
        action_type=action_type,
        source_strategy=strategy,
        target_role=NotBlankStr(target_role) if target_role else None,
        target_agent_id=(NotBlankStr(target_agent_id) if target_agent_id else None),
        rationale=NotBlankStr(rationale),
        confidence=0.8,
        created_at=created_at,
    )


def _action(
    *,
    decision_id: str,
    outcome: ScalingOutcome,
    result_id: str | None = "r-1",
    reason: str | None = None,
    executed_at: datetime = _NOW,
) -> ScalingActionRecord:
    return ScalingActionRecord(
        decision_id=NotBlankStr(decision_id),
        outcome=outcome,
        result_id=NotBlankStr(result_id) if result_id else None,
        reason=NotBlankStr(reason) if reason else None,
        executed_at=executed_at,
    )


def _service(
    *,
    decisions: tuple[ScalingDecision, ...] = (),
    actions: tuple[ScalingActionRecord, ...] = (),
) -> MagicMock:
    service = MagicMock()
    service.get_recent_decisions = MagicMock(return_value=decisions)
    service.get_recent_actions = MagicMock(return_value=actions)
    return service


class TestAggregate:
    async def test_empty_service_returns_empty_summary(self) -> None:
        agg = ScalingSignalAggregator(service=_service())
        since, until = _window()
        summary = await agg.aggregate(since=since, until=until)
        assert summary == OrgScalingSummary()

    async def test_joins_decisions_with_actions(self) -> None:
        decisions = (
            _decision(
                decision_id="d-hire",
                action_type=ScalingActionType.HIRE,
                strategy=ScalingStrategyName.WORKLOAD,
            ),
            _decision(
                decision_id="d-prune",
                action_type=ScalingActionType.PRUNE,
                strategy=ScalingStrategyName.PERFORMANCE_PRUNING,
                target_role=None,
                target_agent_id="agent-1",
            ),
        )
        actions = (
            _action(
                decision_id="d-hire",
                outcome=ScalingOutcome.EXECUTED,
            ),
            _action(
                decision_id="d-prune",
                outcome=ScalingOutcome.FAILED,
                result_id=None,
                reason="offboarding vetoed",
            ),
        )
        agg = ScalingSignalAggregator(
            service=_service(decisions=decisions, actions=actions),
        )
        since, until = _window()
        summary = await agg.aggregate(since=since, until=until)

        assert summary.total_decisions == 2
        assert summary.success_rate == 0.5
        # Assert the join by decision_id explicitly so a positional
        # bug (pairing by index instead of by id) would fail loudly.
        outcome_by_decision = {
            s.decision_id: s.outcome for s in summary.recent_decisions
        }
        assert outcome_by_decision == {
            "d-hire": "executed",
            "d-prune": "failed",
        }
        # Counter.most_common preserves insertion order for ties, so
        # the first-inserted strategy (workload) wins deterministically.
        assert summary.most_common_signal == "workload"

    async def test_decision_without_action_is_pending(self) -> None:
        decisions = (_decision(decision_id="d-orphan"),)
        agg = ScalingSignalAggregator(service=_service(decisions=decisions))
        since, until = _window()
        summary = await agg.aggregate(since=since, until=until)

        assert summary.total_decisions == 1
        assert summary.success_rate == 0.0
        assert summary.recent_decisions[0].decision_id == "d-orphan"
        assert summary.recent_decisions[0].outcome == "pending"

    async def test_filters_outside_window(self) -> None:
        past = _NOW - timedelta(days=30)
        future = _NOW + timedelta(days=30)
        decisions = (
            _decision(decision_id="d-past", created_at=past),
            _decision(decision_id="d-now", created_at=_NOW),
            _decision(decision_id="d-future", created_at=future),
        )
        actions = (_action(decision_id="d-now", outcome=ScalingOutcome.EXECUTED),)
        agg = ScalingSignalAggregator(
            service=_service(decisions=decisions, actions=actions),
        )
        since, until = _window()
        summary = await agg.aggregate(since=since, until=until)
        assert summary.total_decisions == 1
        assert summary.recent_decisions[0].outcome == "executed"

    async def test_window_is_half_open_inclusive_start_exclusive_end(
        self,
    ) -> None:
        """Boundaries: decision at ``since`` included, at ``until`` excluded."""
        since = _NOW
        until = _NOW + timedelta(hours=1)
        decisions = (
            _decision(
                decision_id="d-at-since",
                created_at=since,
            ),
            _decision(
                decision_id="d-just-before-until",
                created_at=until - timedelta(microseconds=1),
            ),
            _decision(
                decision_id="d-at-until",
                created_at=until,
            ),
        )
        agg = ScalingSignalAggregator(service=_service(decisions=decisions))
        summary = await agg.aggregate(since=since, until=until)
        assert summary.total_decisions == 2

    async def test_most_common_signal_picks_top_strategy(self) -> None:
        workload_decisions = tuple(
            _decision(
                decision_id=f"d-{i}",
                strategy=ScalingStrategyName.WORKLOAD,
            )
            for i in range(3)
        )
        decisions = (
            *workload_decisions,
            _decision(
                decision_id="d-skill",
                strategy=ScalingStrategyName.SKILL_GAP,
            ),
        )
        agg = ScalingSignalAggregator(service=_service(decisions=decisions))
        since, until = _window()
        summary = await agg.aggregate(since=since, until=until)
        assert summary.most_common_signal == "workload"
        assert summary.total_decisions == 4

    async def test_service_exception_returns_empty_and_logs(self) -> None:
        service = MagicMock(spec=ScalingService)
        service.get_recent_decisions = MagicMock(
            side_effect=RuntimeError("boom"),
        )
        service.get_recent_actions = MagicMock(return_value=())
        agg = ScalingSignalAggregator(service=service)
        since, until = _window()

        with structlog.testing.capture_logs() as cap:
            summary = await agg.aggregate(since=since, until=until)

        assert summary is not None
        assert isinstance(summary, OrgScalingSummary)
        assert summary == OrgScalingSummary()
        failures = [e for e in cap if e.get("event") == META_SIGNAL_AGGREGATION_FAILED]
        assert len(failures) == 1

    async def test_local_aggregation_bugs_propagate(self) -> None:
        """Bugs in local reducing must not be swallowed as empty summaries.

        Only :class:`ScalingService` fetch errors are mapped to
        ``_EMPTY``; errors in the local filter/join/reduce steps must
        propagate so they surface through normal error handling rather
        than being masked as "no activity".  A decision with a
        non-datetime ``created_at`` triggers the filter comparison to
        raise, and we assert the ``TypeError`` escapes ``aggregate``
        without the broad fallback catching it -- and that the reducer
        logs a ``META_SIGNAL_AGGREGATION_FAILED`` event with the
        ``stage="reduce"`` tag before re-raising so operators see the
        failure in observability.
        """
        broken = MagicMock()
        broken.created_at = MagicMock()  # not a datetime -> comparison fails
        service = MagicMock(spec=ScalingService)
        service.get_recent_decisions = MagicMock(return_value=(broken,))
        service.get_recent_actions = MagicMock(return_value=())
        agg = ScalingSignalAggregator(service=service)
        since, until = _window()

        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(TypeError),
        ):
            await agg.aggregate(since=since, until=until)

        failures = [
            e
            for e in cap
            if e.get("event") == META_SIGNAL_AGGREGATION_FAILED
            and e.get("stage") == "reduce"
        ]
        assert len(failures) == 1
        assert failures[0]["decision_count"] == 1

    async def test_logs_completed_on_success(self) -> None:
        decisions = (_decision(),)
        actions = (_action(decision_id="d-001", outcome=ScalingOutcome.EXECUTED),)
        agg = ScalingSignalAggregator(
            service=_service(decisions=decisions, actions=actions),
        )
        since, until = _window()
        with structlog.testing.capture_logs() as cap:
            await agg.aggregate(since=since, until=until)
        events = [e for e in cap if e.get("event") == META_SIGNAL_AGGREGATION_COMPLETED]
        assert any(
            e.get("total_decisions") == 1 and e.get("success_rate") == 1.0
            for e in events
        )

    def test_domain_is_scaling(self) -> None:
        agg = ScalingSignalAggregator(service=_service())
        assert agg.domain == "scaling"
