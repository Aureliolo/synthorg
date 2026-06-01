# mypy: disable-error-code="explicit-any"
"""Shared fixtures for scaling unit tests."""

from datetime import UTC, datetime
from typing import Any

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import (
    ScalingActionType,
    ScalingStrategyName,
)
from synthorg.hr.scaling.models import (
    ScalingContext,
    ScalingDecision,
    ScalingSignal,
)

NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)


def make_signal(
    *,
    name: str = "avg_utilization",
    value: float = 0.75,
    threshold: float | None = 0.85,
    source: str = "workload",
    timestamp: datetime | None = None,
) -> ScalingSignal:
    """Create a ScalingSignal with sensible defaults."""
    return ScalingSignal(
        name=NotBlankStr(name),
        value=value,
        threshold=threshold,
        source=NotBlankStr(source),
        timestamp=timestamp or NOW,
    )


def make_context(  # noqa: PLR0913
    *,
    agent_ids: tuple[str, ...] = (
        "agent-001",
        "agent-002",
        "agent-003",
        "agent-004",
        "agent-005",
    ),
    workload_signals: tuple[ScalingSignal, ...] = (),
    budget_signals: tuple[ScalingSignal, ...] = (),
    performance_signals: tuple[ScalingSignal, ...] = (),
    skill_signals: tuple[ScalingSignal, ...] = (),
    benchmark_signals: tuple[ScalingSignal, ...] = (),
    performance_snapshots: dict[str, Any] | None = None,
    evaluated_at: datetime | None = None,
) -> ScalingContext:
    """Create a ScalingContext with sensible defaults."""
    return ScalingContext(
        agent_ids=tuple(NotBlankStr(a) for a in agent_ids),
        workload_signals=workload_signals,
        budget_signals=budget_signals,
        performance_signals=performance_signals,
        skill_signals=skill_signals,
        benchmark_signals=benchmark_signals,
        performance_snapshots=performance_snapshots or {},
        evaluated_at=evaluated_at or NOW,
    )


def make_decision(  # noqa: PLR0913
    *,
    action_type: ScalingActionType = ScalingActionType.HIRE,
    source_strategy: ScalingStrategyName = ScalingStrategyName.WORKLOAD,
    target_agent_id: str | None = None,
    target_role: str | None = "backend_developer",
    target_skills: tuple[str, ...] = (),
    target_department: str | None = "engineering",
    rationale: str = "utilization above threshold",
    confidence: float = 0.8,
    signals: tuple[ScalingSignal, ...] = (),
    created_at: datetime | None = None,
) -> ScalingDecision:
    """Create a ScalingDecision with sensible defaults."""
    return ScalingDecision(
        action_type=action_type,
        source_strategy=source_strategy,
        target_agent_id=(NotBlankStr(target_agent_id) if target_agent_id else None),
        target_role=NotBlankStr(target_role) if target_role else None,
        target_skills=tuple(NotBlankStr(s) for s in target_skills),
        target_department=(
            NotBlankStr(target_department) if target_department else None
        ),
        rationale=NotBlankStr(rationale),
        confidence=confidence,
        signals=signals,
        created_at=created_at or NOW,
    )
