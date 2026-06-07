"""Shared fixtures and factories for pruning unit tests."""

from datetime import UTC, datetime

from pydantic import AwareDatetime

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    TrendResult,
    WindowMetrics,
)
from tests._shared import as_uuid

NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=UTC)


# ── Performance Snapshot Factories ─────────────────────────────


def make_window_metrics(  # noqa: PLR0913
    *,
    window_size: str = "7d",
    data_point_count: int = 10,
    tasks_completed: int = 8,
    tasks_failed: int = 2,
    avg_quality_score: float | None = 5.0,
    avg_cost_per_task: float | None = 0.5,
    avg_completion_time_seconds: float | None = 120.0,
    avg_tokens_per_task: float | None = 500.0,
    success_rate: float | None = 0.8,
    collaboration_score: float | None = 5.0,
    currency: CurrencyCode = DEFAULT_CURRENCY,
) -> WindowMetrics:
    """Build a WindowMetrics with sensible defaults."""
    return WindowMetrics(
        window_size=NotBlankStr(window_size),
        data_point_count=data_point_count,
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
        avg_quality_score=avg_quality_score,
        avg_cost_per_task=avg_cost_per_task,
        avg_completion_time_seconds=avg_completion_time_seconds,
        avg_tokens_per_task=avg_tokens_per_task,
        success_rate=success_rate,
        collaboration_score=collaboration_score,
        currency=currency,
    )


def make_trend_result(
    *,
    metric_name: str = "quality_score",
    window_size: str = "7d",
    direction: TrendDirection = TrendDirection.STABLE,
    slope: float = 0.0,
    data_point_count: int = 10,
) -> TrendResult:
    """Build a TrendResult with sensible defaults."""
    return TrendResult(
        metric_name=NotBlankStr(metric_name),
        window_size=NotBlankStr(window_size),
        direction=direction,
        slope=slope,
        data_point_count=data_point_count,
    )


def make_performance_snapshot(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    computed_at: AwareDatetime | None = None,
    windows: tuple[WindowMetrics, ...] = (),
    trends: tuple[TrendResult, ...] = (),
    overall_quality_score: float | None = 5.0,
    overall_collaboration_score: float | None = 5.0,
) -> AgentPerformanceSnapshot:
    """Build an AgentPerformanceSnapshot with sensible defaults."""
    return AgentPerformanceSnapshot(
        agent_id=NotBlankStr(agent_id),
        computed_at=computed_at or NOW,
        windows=windows,
        trends=trends,
        overall_quality_score=overall_quality_score,
        overall_collaboration_score=overall_collaboration_score,
    )


# ── Approval Factories ────────────────────────────────────────


def make_approval_item(  # noqa: PLR0913
    *,
    approval_id: str | None = None,
    action_type: str = "hr:prune",
    title: str = "Prune agent test-agent",
    description: str = "Policy threshold: quality below threshold",
    requested_by: str = "system",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.CRITICAL,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    created_at: AwareDatetime | None = None,
    expires_at: AwareDatetime | None = None,
    decided_at: AwareDatetime | None = None,
    decided_by: str | None = None,
    decision_reason: str | None = None,
    metadata: dict[str, str] | None = None,
) -> ApprovalItem:
    """Build an ApprovalItem for pruning with sensible defaults."""
    return ApprovalItem(
        id=as_uuid(approval_id) if approval_id else as_uuid("pruning-approval-default"),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr(title),
        description=NotBlankStr(description),
        requested_by=NotBlankStr(requested_by),
        risk_level=risk_level,
        status=status,
        created_at=created_at or NOW,
        expires_at=expires_at,
        decided_at=decided_at,
        decided_by=NotBlankStr(decided_by) if decided_by else None,
        decision_reason=(NotBlankStr(decision_reason) if decision_reason else None),
        metadata=metadata or {"agent_id": "agent-001", "policy_name": "threshold"},
    )
