"""Unit test configuration and fixtures for evaluation framework."""

from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.models import (
    EvaluationContext,
    InteractionFeedback,
    PillarScore,
    ResilienceMetrics,
)
from synthorg.hr.performance.models import AgentPerformanceSnapshot, WindowMetrics


def make_pillar_score(
    *,
    pillar: EvaluationPillar = EvaluationPillar.INTELLIGENCE,
    score: float = 7.5,
    confidence: float = 0.8,
    strategy_name: str = "test_strategy",
    data_point_count: int = 10,
    evaluated_at: datetime | None = None,
) -> PillarScore:
    """Build a PillarScore with sensible defaults."""
    return PillarScore(
        pillar=pillar,
        score=score,
        confidence=confidence,
        strategy_name=NotBlankStr(strategy_name),
        data_point_count=data_point_count,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )


def make_interaction_feedback(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    task_id: str | None = None,
    recorded_at: datetime | None = None,
    clarity_rating: float | None = 0.8,
    tone_rating: float | None = 0.7,
    helpfulness_rating: float | None = 0.9,
    trust_rating: float | None = 0.85,
    satisfaction_rating: float | None = 0.8,
    source: str = "human",
) -> InteractionFeedback:
    """Build an InteractionFeedback with sensible defaults."""
    return InteractionFeedback(
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id) if task_id else None,
        recorded_at=recorded_at or datetime.now(UTC),
        clarity_rating=clarity_rating,
        tone_rating=tone_rating,
        helpfulness_rating=helpfulness_rating,
        trust_rating=trust_rating,
        satisfaction_rating=satisfaction_rating,
        source=NotBlankStr(source),
    )


def make_resilience_metrics(
    *,
    total_tasks: int = 20,
    failed_tasks: int = 3,
    recovered_tasks: int = 2,
    current_success_streak: int = 5,
    longest_success_streak: int = 10,
    quality_score_stddev: float | None = 1.2,
) -> ResilienceMetrics:
    """Build a ResilienceMetrics with sensible defaults."""
    return ResilienceMetrics(
        total_tasks=total_tasks,
        failed_tasks=failed_tasks,
        recovered_tasks=recovered_tasks,
        current_success_streak=current_success_streak,
        longest_success_streak=longest_success_streak,
        quality_score_stddev=quality_score_stddev,
    )


def make_snapshot(
    *,
    agent_id: str = "agent-001",
    computed_at: datetime | None = None,
    overall_quality_score: float | None = 7.5,
    overall_collaboration_score: float | None = 6.8,
    windows: tuple[WindowMetrics, ...] | None = None,
) -> AgentPerformanceSnapshot:
    """Build an AgentPerformanceSnapshot with sensible defaults."""
    if windows is None:
        windows = (
            WindowMetrics(
                window_size=NotBlankStr("30d"),
                data_point_count=15,
                tasks_completed=12,
                tasks_failed=3,
                avg_quality_score=7.5,
                avg_cost_per_task=5.0,
                avg_completion_time_seconds=120.0,
                avg_tokens_per_task=2000.0,
                success_rate=0.8,
                currency="USD",
            ),
        )
    return AgentPerformanceSnapshot(
        agent_id=NotBlankStr(agent_id),
        computed_at=computed_at or datetime.now(UTC),
        windows=windows,
        overall_quality_score=overall_quality_score,
        overall_collaboration_score=overall_collaboration_score,
    )


def make_evaluation_context(
    *,
    agent_id: str = "agent-001",
    now: datetime | None = None,
    config: EvaluationConfig | None = None,
    snapshot: AgentPerformanceSnapshot | None = None,
) -> EvaluationContext:
    """Build an EvaluationContext with sensible defaults."""
    return EvaluationContext(
        agent_id=NotBlankStr(agent_id),
        now=now or datetime.now(UTC),
        config=config or EvaluationConfig(),
        snapshot=snapshot or make_snapshot(agent_id=agent_id),
    )
