"""Tests for EvaluationService orchestrator."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import (
    EfficiencyConfig,
    EvaluationConfig,
    ExperienceConfig,
    GovernanceConfig,
    ResilienceConfig,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.evaluator import EvaluationService
from synthorg.hr.performance.tracker import PerformanceTracker
from tests.unit.hr.evaluation.conftest import (
    make_interaction_feedback,
    make_snapshot,
)
from tests.unit.hr.performance.conftest import make_task_metric

pytestmark = pytest.mark.unit


@pytest.fixture
def tracker() -> PerformanceTracker:
    return PerformanceTracker()


@pytest.fixture
def service(tracker: PerformanceTracker) -> EvaluationService:
    return EvaluationService(tracker=tracker)


class TestEvaluationService:
    """EvaluationService orchestration tests."""

    async def test_evaluate_default_config(
        self,
        service: EvaluationService,
        tracker: PerformanceTracker,
    ) -> None:
        """Default config evaluates all 5 pillars."""
        agent_id = NotBlankStr("agent-001")
        for i in range(10):
            await tracker.record_task_metric(
                make_task_metric(
                    agent_id="agent-001",
                    task_id=f"task-{i:03d}",
                    is_success=i < 8,
                    quality_score=7.0 + i * 0.2,
                ),
            )

        report = await service.evaluate(agent_id)
        assert report.agent_id == "agent-001"
        assert len(report.pillar_scores) == 5
        assert report.overall_score >= 0.0
        assert report.overall_score <= 10.0
        assert report.overall_confidence >= 0.0
        assert report.overall_confidence <= 1.0
        # All 5 pillars represented.
        pillars = {ps.pillar for ps in report.pillar_scores}
        assert pillars == set(EvaluationPillar)

    async def test_evaluate_with_disabled_pillar(
        self,
        tracker: PerformanceTracker,
    ) -> None:
        """Disabled pillar is skipped, weights redistributed."""
        cfg = EvaluationConfig(
            experience=ExperienceConfig(enabled=False),
        )
        svc = EvaluationService(tracker=tracker, config=cfg)
        agent_id = NotBlankStr("agent-001")
        await tracker.record_task_metric(
            make_task_metric(agent_id="agent-001", quality_score=7.0),
        )

        report = await svc.evaluate(agent_id)
        assert len(report.pillar_scores) == 4
        pillars = {ps.pillar for ps in report.pillar_scores}
        assert EvaluationPillar.EXPERIENCE not in pillars
        # Weights should sum to ~1.0.
        weight_sum = sum(w for _, w in report.pillar_weights)
        assert abs(weight_sum - 1.0) < 0.01

    async def test_evaluate_multiple_pillars_disabled(
        self,
        tracker: PerformanceTracker,
    ) -> None:
        """Multiple disabled pillars -- only enabled ones scored."""
        cfg = EvaluationConfig(
            resilience=ResilienceConfig(enabled=False),
            governance=GovernanceConfig(enabled=False),
            experience=ExperienceConfig(enabled=False),
        )
        svc = EvaluationService(tracker=tracker, config=cfg)
        agent_id = NotBlankStr("agent-001")
        await tracker.record_task_metric(
            make_task_metric(agent_id="agent-001", quality_score=8.0),
        )

        report = await svc.evaluate(agent_id)
        assert len(report.pillar_scores) == 2
        pillars = {ps.pillar for ps in report.pillar_scores}
        assert pillars == {
            EvaluationPillar.INTELLIGENCE,
            EvaluationPillar.EFFICIENCY,
        }

    async def test_efficiency_pillar_via_configurable_scorer(
        self,
        service: EvaluationService,
        tracker: PerformanceTracker,
    ) -> None:
        """Efficiency pillar is now produced by the standard composite path."""
        agent_id = NotBlankStr("agent-001")
        for i in range(6):
            await tracker.record_task_metric(
                make_task_metric(
                    agent_id="agent-001",
                    task_id=f"task-{i:03d}",
                    cost=2.0,
                    duration_seconds=60.0,
                    tokens_used=1000,
                ),
            )

        report = await service.evaluate(agent_id)
        eff_scores = [
            ps
            for ps in report.pillar_scores
            if ps.pillar == EvaluationPillar.EFFICIENCY
        ]
        assert len(eff_scores) == 1
        assert eff_scores[0].strategy_name == "configurable[efficiency]"

    async def test_efficiency_metric_toggles(
        self,
        tracker: PerformanceTracker,
    ) -> None:
        """Efficiency respects metric toggles."""
        cfg = EvaluationConfig(
            efficiency=EfficiencyConfig(tokens_enabled=False),
        )
        svc = EvaluationService(tracker=tracker, config=cfg)
        agent_id = NotBlankStr("agent-001")
        for i in range(6):
            await tracker.record_task_metric(
                make_task_metric(
                    agent_id="agent-001",
                    task_id=f"task-{i:03d}",
                    cost=2.0,
                    duration_seconds=60.0,
                    tokens_used=1000,
                ),
            )

        report = await svc.evaluate(agent_id)
        eff = next(
            ps
            for ps in report.pillar_scores
            if ps.pillar == EvaluationPillar.EFFICIENCY
        )
        assert not any(k == "tokens" for k, _ in eff.breakdown)

    async def test_efficiency_7d_window_fallback(
        self,
        tracker: PerformanceTracker,
    ) -> None:
        """Efficiency uses 7d window when 30d is absent."""
        from synthorg.hr.evaluation.configurable_scorer import (
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.efficiency import (
            EfficiencyMetricExtractor,
        )
        from synthorg.hr.evaluation.models import EvaluationContext
        from synthorg.hr.performance.models import WindowMetrics

        snapshot = make_snapshot(
            windows=(
                WindowMetrics(
                    window_size=NotBlankStr("7d"),
                    data_point_count=5,
                    tasks_completed=4,
                    tasks_failed=1,
                    avg_quality_score=7.0,
                    avg_cost_per_task=3.0,
                    avg_completion_time_seconds=100.0,
                    avg_tokens_per_task=1500.0,
                    success_rate=0.8,
                    currency="EUR",
                ),
            ),
        )
        ctx = EvaluationContext(
            agent_id=NotBlankStr("agent-001"),
            now=datetime.now(UTC),
            config=EvaluationConfig(),
            snapshot=snapshot,
        )
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.EFFICIENCY,
            EfficiencyMetricExtractor(),
        )
        result = await scorer.score(context=ctx)
        assert result.score > 0.0
        assert result.confidence > 0.0

    async def test_efficiency_no_window_returns_neutral(
        self,
        tracker: PerformanceTracker,
    ) -> None:
        """Efficiency returns neutral when no windows are available."""
        from synthorg.hr.evaluation.configurable_scorer import (
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.efficiency import (
            EfficiencyMetricExtractor,
        )
        from synthorg.hr.evaluation.models import EvaluationContext

        snapshot = make_snapshot(windows=())
        ctx = EvaluationContext(
            agent_id=NotBlankStr("agent-001"),
            now=datetime.now(UTC),
            config=EvaluationConfig(),
            snapshot=snapshot,
        )
        scorer = ConfigurablePillarScorer(
            EvaluationPillar.EFFICIENCY,
            EfficiencyMetricExtractor(),
        )
        result = await scorer.score(context=ctx)
        assert result.score == 5.0
        assert result.confidence == 0.0

    async def test_efficiency_cost_exceeds_reference_clamps_to_zero(
        self,
        tracker: PerformanceTracker,
    ) -> None:
        """Efficiency sub-score clamps to 0.0 when cost > reference."""
        agent_id = NotBlankStr("agent-001")
        for i in range(6):
            await tracker.record_task_metric(
                make_task_metric(
                    agent_id="agent-001",
                    task_id=f"task-{i:03d}",
                    cost=15.0,  # Above default reference of 10.0.
                    duration_seconds=60.0,
                    tokens_used=1000,
                ),
            )
        cfg = EvaluationConfig(
            efficiency=EfficiencyConfig(
                time_enabled=False,
                tokens_enabled=False,
            ),
        )
        svc = EvaluationService(tracker=tracker, config=cfg)
        report = await svc.evaluate(agent_id)
        eff = next(
            ps
            for ps in report.pillar_scores
            if ps.pillar == EvaluationPillar.EFFICIENCY
        )
        # Cost exceeds reference -- score should be 0.0.
        assert eff.score == 0.0

    async def test_feedback_recording_and_retrieval(
        self,
        service: EvaluationService,
    ) -> None:
        fb1 = make_interaction_feedback(agent_id="agent-001")
        fb2 = make_interaction_feedback(agent_id="agent-002")
        service.record_feedback(fb1)
        service.record_feedback(fb2)

        all_fb = service.get_feedback()
        assert len(all_fb) == 2

        agent1_fb = service.get_feedback(agent_id=NotBlankStr("agent-001"))
        assert len(agent1_fb) == 1
        assert agent1_fb[0].agent_id == "agent-001"

    async def test_feedback_since_filter(
        self,
        service: EvaluationService,
    ) -> None:
        now = datetime.now(UTC)
        old = make_interaction_feedback(
            agent_id="agent-001",
            recorded_at=now - timedelta(days=7),
        )
        recent = make_interaction_feedback(
            agent_id="agent-001",
            recorded_at=now,
        )
        service.record_feedback(old)
        service.record_feedback(recent)

        result = service.get_feedback(since=now - timedelta(days=1))
        assert len(result) == 1

    async def test_report_has_unique_id(
        self,
        service: EvaluationService,
        tracker: PerformanceTracker,
    ) -> None:
        agent_id = NotBlankStr("agent-001")
        await tracker.record_task_metric(
            make_task_metric(agent_id="agent-001"),
        )
        r1 = await service.evaluate(agent_id)
        r2 = await service.evaluate(agent_id)
        assert r1.id != r2.id


class TestComputeResilienceMetrics:
    """Tests for the static _compute_resilience_metrics method."""

    def test_empty_records(self) -> None:
        rm = EvaluationService._compute_resilience_metrics(())
        assert rm.total_tasks == 0
        assert rm.failed_tasks == 0
        assert rm.current_success_streak == 0

    def test_all_successes(self) -> None:
        now = datetime.now(UTC)
        records = tuple(
            make_task_metric(
                task_id=f"t-{i}",
                is_success=True,
                quality_score=8.0,
                completed_at=now + timedelta(minutes=i),
            )
            for i in range(5)
        )
        rm = EvaluationService._compute_resilience_metrics(records)
        assert rm.total_tasks == 5
        assert rm.failed_tasks == 0
        assert rm.recovered_tasks == 0
        assert rm.current_success_streak == 5
        assert rm.longest_success_streak == 5

    def test_failure_recovery_pattern(self) -> None:
        """S S F S S F S -- 2 failures, 2 recoveries."""
        now = datetime.now(UTC)
        pattern = [True, True, False, True, True, False, True]
        records = tuple(
            make_task_metric(
                task_id=f"t-{i}",
                is_success=s,
                completed_at=now + timedelta(minutes=i),
            )
            for i, s in enumerate(pattern)
        )
        rm = EvaluationService._compute_resilience_metrics(records)
        assert rm.total_tasks == 7
        assert rm.failed_tasks == 2
        assert rm.recovered_tasks == 2
        assert rm.current_success_streak == 1
        assert rm.longest_success_streak == 2

    def test_quality_stddev_computation(self) -> None:
        now = datetime.now(UTC)
        records = tuple(
            make_task_metric(
                task_id=f"t-{i}",
                quality_score=float(score),
                completed_at=now + timedelta(minutes=i),
            )
            for i, score in enumerate([6.0, 8.0, 6.0, 8.0])
        )
        rm = EvaluationService._compute_resilience_metrics(records)
        assert rm.quality_score_stddev is not None
        assert rm.quality_score_stddev > 0.0

    def test_quality_stddev_none_with_single_scored(self) -> None:
        now = datetime.now(UTC)
        records = (
            make_task_metric(
                task_id="t-0",
                quality_score=7.0,
                completed_at=now,
            ),
        )
        rm = EvaluationService._compute_resilience_metrics(records)
        assert rm.quality_score_stddev is None

    def test_shuffled_records_sorted_by_completion_time(self) -> None:
        """Records arriving out of order are sorted before processing."""
        now = datetime.now(UTC)
        # Create records in reverse order.
        records = tuple(
            make_task_metric(
                task_id=f"t-{i}",
                is_success=True,
                completed_at=now + timedelta(minutes=4 - i),
            )
            for i in range(5)
        )
        rm = EvaluationService._compute_resilience_metrics(records)
        assert rm.current_success_streak == 5
        assert rm.longest_success_streak == 5

    def test_pattern_ending_in_failures(self) -> None:
        """S S F F -- streak resets, longest is 2."""
        now = datetime.now(UTC)
        pattern = [True, True, False, False]
        records = tuple(
            make_task_metric(
                task_id=f"t-{i}",
                is_success=s,
                completed_at=now + timedelta(minutes=i),
            )
            for i, s in enumerate(pattern)
        )
        rm = EvaluationService._compute_resilience_metrics(records)
        assert rm.total_tasks == 4
        assert rm.failed_tasks == 2
        assert rm.recovered_tasks == 0
        assert rm.current_success_streak == 0
        assert rm.longest_success_streak == 2

    async def test_explicit_now_threads_to_report(self) -> None:
        """Explicit now parameter appears in the report's computed_at."""
        tracker = PerformanceTracker()
        svc = EvaluationService(tracker=tracker)
        agent_id = NotBlankStr("agent-001")
        await tracker.record_task_metric(
            make_task_metric(agent_id="agent-001"),
        )
        explicit_now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        report = await svc.evaluate(agent_id, now=explicit_now)
        assert report.computed_at == explicit_now


class TestFeedbackToEvaluationPipeline:
    """End-to-end: record feedback then evaluate to verify UX pillar."""

    async def test_feedback_flows_into_evaluation(self) -> None:
        """Recorded feedback reaches the experience pillar in evaluation."""
        from synthorg.hr.evaluation.config import ExperienceConfig

        cfg = EvaluationConfig(
            experience=ExperienceConfig(min_feedback_count=1),
        )
        tracker = PerformanceTracker()
        svc = EvaluationService(tracker=tracker, config=cfg)
        agent_id = NotBlankStr("agent-001")

        await tracker.record_task_metric(
            make_task_metric(agent_id="agent-001", quality_score=7.0),
        )

        for _ in range(3):
            svc.record_feedback(
                make_interaction_feedback(
                    agent_id="agent-001",
                    clarity_rating=0.9,
                    helpfulness_rating=0.8,
                ),
            )

        report = await svc.evaluate(agent_id)
        ux = next(
            ps
            for ps in report.pillar_scores
            if ps.pillar == EvaluationPillar.EXPERIENCE
        )
        assert ux.confidence > 0.0
        assert ux.score > 5.0
