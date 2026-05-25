# mypy: disable-error-code="explicit-any"
"""Tests for EvalLoopCoordinator."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.engine.trajectory.scorer import TrajectoryScorer
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.dogfooding_dataset_builder import DogfoodingDatasetBuilder
from synthorg.hr.evaluation.evaluator import EvaluationService
from synthorg.hr.evaluation.external_benchmark_registry import ExternalBenchmarkRegistry
from synthorg.hr.evaluation.loop_coordinator import (
    _DEFAULT_PATTERN_ACTIONS,
    EvalLoopCoordinator,
)
from synthorg.hr.evaluation.models import EvaluationReport
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.training.service import TrainingService
from tests._shared import mock_of


def _make_coordinator(
    *,
    config: EvalLoopConfig | None = None,
    task_metrics: tuple[MagicMock, ...] = (),
    eval_report: MagicMock | None = None,
) -> EvalLoopCoordinator:
    """Build an EvalLoopCoordinator with mocked dependencies."""
    tracker = mock_of[PerformanceTracker](
        get_task_metrics=MagicMock(return_value=task_metrics),
    )

    evaluate_return = eval_report if eval_report is not None else None
    evaluation = mock_of[EvaluationService](
        evaluate=AsyncMock(return_value=evaluate_return),
    )

    scorer = mock_of[TrajectoryScorer]()
    training = mock_of[TrainingService]()
    dataset_builder = mock_of[DogfoodingDatasetBuilder]()
    benchmark_registry = mock_of[ExternalBenchmarkRegistry](
        list_registered=MagicMock(return_value=()),
    )

    return EvalLoopCoordinator(
        performance_tracker=tracker,
        evaluation_service=evaluation,
        trajectory_scorer=scorer,
        training_service=training,
        dataset_builder=dataset_builder,
        benchmark_registry=benchmark_registry,
        config=config,
    )


def _make_task_record(agent_id: str = "agent-1") -> MagicMock:
    record = MagicMock()
    record.agent_id = agent_id
    return record


@pytest.mark.unit
class TestEvalLoopCoordinatorInit:
    """EvalLoopCoordinator construction."""

    def test_default_config(self) -> None:
        coordinator = _make_coordinator()
        assert coordinator.config.enabled is True

    def test_custom_config(self) -> None:
        config = EvalLoopConfig(enabled=False)
        coordinator = _make_coordinator(config=config)
        assert coordinator.config.enabled is False


@pytest.mark.unit
class TestEvalLoopCoordinatorRunCycle:
    """EvalLoopCoordinator.run_cycle()."""

    async def test_run_cycle_empty_window(self) -> None:
        coordinator = _make_coordinator()
        report = await coordinator.run_cycle(window=timedelta(hours=1))
        assert report.agents_evaluated == 0
        assert report.agent_reports == ()
        assert report.observations == ()
        assert report.proposed_actions == ()
        assert report.training_triggered is False

    async def test_run_cycle_with_agents(self) -> None:
        records = (
            _make_task_record("agent-1"),
            _make_task_record("agent-2"),
            _make_task_record("agent-1"),  # duplicate
        )
        coordinator = _make_coordinator(task_metrics=records)
        report = await coordinator.run_cycle(window=timedelta(hours=1))
        # 2 unique agents
        assert report.agents_evaluated == 2

    async def test_run_cycle_with_explicit_agent_ids(self) -> None:
        coordinator = _make_coordinator()
        report = await coordinator.run_cycle(
            window=timedelta(hours=1),
            agent_ids=("agent-x",),
        )
        assert report.agents_evaluated == 1

    async def test_run_cycle_report_timing(self) -> None:
        coordinator = _make_coordinator()
        before = datetime.now(UTC)
        report = await coordinator.run_cycle(window=timedelta(hours=1))
        assert report.duration_seconds >= 0.0
        assert report.window_end >= before
        assert report.window_start < report.window_end

    async def test_run_cycle_no_reports_no_patterns(self) -> None:
        coordinator = _make_coordinator()
        report = await coordinator.run_cycle(window=timedelta(hours=1))
        assert report.observations == ()
        assert report.proposed_actions == ()

    async def test_run_cycle_no_benchmarks_by_default(self) -> None:
        coordinator = _make_coordinator()
        report = await coordinator.run_cycle(window=timedelta(hours=1))
        assert report.benchmark_results == ()

    async def test_run_cycle_benchmarks_when_enabled(self) -> None:
        config = EvalLoopConfig(benchmark_on_cycle=True)
        coordinator = _make_coordinator(config=config)
        report = await coordinator.run_cycle(window=timedelta(hours=1))
        assert report.benchmark_results == ()  # No benchmarks registered


@pytest.mark.unit
class TestEvalLoopCoordinatorEvaluateOne:
    """EvalLoopCoordinator._evaluate_one() isolation."""

    async def test_evaluate_one_success(self) -> None:
        mock_report = MagicMock()
        coordinator = _make_coordinator(eval_report=mock_report)
        result = await coordinator._evaluate_one("agent-1")
        assert result is mock_report

    async def test_evaluate_one_failure_returns_none(self) -> None:
        coordinator = _make_coordinator()
        coordinator._evaluation.evaluate = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("eval failed"),
        )
        result = await coordinator._evaluate_one("agent-1")
        assert result is None


@pytest.mark.unit
class TestEvalLoopCoordinatorCollectAgentIds:
    """EvalLoopCoordinator._collect_agent_ids()."""

    def test_deduplicates_ids(self) -> None:
        records = (
            _make_task_record("a"),
            _make_task_record("b"),
            _make_task_record("a"),
        )
        coordinator = _make_coordinator(task_metrics=records)
        ids = coordinator._collect_agent_ids(
            since=datetime(2020, 1, 1, tzinfo=UTC),
        )
        assert ids == ("a", "b")

    def test_empty_records(self) -> None:
        coordinator = _make_coordinator()
        ids = coordinator._collect_agent_ids(
            since=datetime(2020, 1, 1, tzinfo=UTC),
        )
        assert ids == ()


def _make_pillar_score(pillar: str, score: float) -> Any:
    """Build a concrete test double with the shape ``_identify_patterns`` reads.

    Using ``SimpleNamespace`` (not ``MagicMock``) means a misspelled
    accessor (e.g. ``score.scoree`` instead of ``score.score``, or
    ``score.pillar.values`` instead of ``score.pillar.value``) raises
    ``AttributeError`` immediately instead of silently returning
    another MagicMock, catching contract drift in the tests themselves.
    """
    return SimpleNamespace(
        pillar=SimpleNamespace(value=pillar),
        score=score,
    )


def _make_report(
    agent_id: str,
    *pillar_scores: tuple[str, float],
) -> EvaluationReport:
    """Build a concrete report test double for ``_identify_patterns``.

    The coordinator only reads ``agent_id`` and ``pillar_scores`` off
    the report, so a structural stub is sufficient. We cast to the
    real type so call sites don't need per-call ``type: ignore`` hints
    while still preserving strict attribute-access checking inside
    the test (hand-built ``SimpleNamespace`` objects raise on typos
    rather than silently returning more mocks).
    """
    stub = SimpleNamespace(
        agent_id=agent_id,
        pillar_scores=tuple(_make_pillar_score(p, s) for p, s in pillar_scores),
    )
    return cast(EvaluationReport, stub)


@pytest.mark.unit
class TestEvalLoopCoordinatorIdentifyPatterns:
    """_identify_patterns() clustering behaviour."""

    async def test_disabled_pattern_identifier_returns_empty(self) -> None:
        config = EvalLoopConfig(pattern_identifier_enabled=False)
        coordinator = _make_coordinator(config=config)
        report = _make_report("a", ("intelligence", 1.0))
        assert await coordinator._identify_patterns((report,)) == ()

    async def test_empty_reports_returns_empty(self) -> None:
        coordinator = _make_coordinator()
        assert await coordinator._identify_patterns(()) == ()

    async def test_clusters_pillars_below_threshold(self) -> None:
        config = EvalLoopConfig(
            pattern_weakness_threshold=5.0,
            pattern_min_agents=2,
        )
        coordinator = _make_coordinator(config=config)
        reports = (
            _make_report("a", ("intelligence", 2.0), ("efficiency", 8.0)),
            _make_report("b", ("intelligence", 3.0), ("efficiency", 7.0)),
            _make_report("c", ("intelligence", 9.0), ("efficiency", 9.0)),
        )
        patterns = await coordinator._identify_patterns(reports)
        assert patterns == ("weakness:intelligence",)

    async def test_sorts_patterns_by_count_desc_then_name(self) -> None:
        config = EvalLoopConfig(
            pattern_weakness_threshold=5.0,
            pattern_min_agents=2,
        )
        coordinator = _make_coordinator(config=config)
        reports = (
            _make_report("a", ("intelligence", 1.0), ("governance", 1.0)),
            _make_report("b", ("intelligence", 2.0), ("governance", 2.0)),
            _make_report("c", ("governance", 2.0)),  # governance has 3 weak agents
        )
        patterns = await coordinator._identify_patterns(reports)
        # governance first (3 weak), then intelligence (2 weak)
        assert patterns == ("weakness:governance", "weakness:intelligence")

    async def test_skips_pillars_below_min_agents(self) -> None:
        config = EvalLoopConfig(
            pattern_weakness_threshold=5.0,
            pattern_min_agents=3,
        )
        coordinator = _make_coordinator(config=config)
        reports = (
            _make_report("a", ("intelligence", 1.0)),
            _make_report("b", ("intelligence", 2.0)),
        )
        assert await coordinator._identify_patterns(reports) == ()

    async def test_duplicate_pillar_in_same_report_counts_once(self) -> None:
        """A single agent report with a duplicated pillar counts once.

        Regression: pre-fix the loop incremented ``weak_counts``
        per ``pillar_score`` entry, so a report that listed the same
        pillar twice (defensive -- the model does not enforce
        uniqueness) would inflate the weak-agent count and
        spuriously satisfy ``pattern_min_agents``. Post-fix the
        coordinator collapses per-agent pillars into a set first.
        """
        config = EvalLoopConfig(
            pattern_weakness_threshold=5.0,
            pattern_min_agents=2,
        )
        coordinator = _make_coordinator(config=config)
        # One agent, one pillar listed twice. min_agents=2 means the
        # pillar should NOT be promoted to a pattern, because only one
        # agent is actually weak on it.
        reports = (
            _make_report(
                "a",
                ("intelligence", 1.0),
                ("intelligence", 2.0),
            ),
        )
        patterns = await coordinator._identify_patterns(reports)
        assert patterns == (), (
            "Duplicate pillar entries from the same agent must not inflate "
            f"weak_counts; got {patterns!r}"
        )


@pytest.mark.unit
class TestEvalLoopCoordinatorProposeActions:
    """_propose_actions() mapping behaviour."""

    async def test_empty_patterns_returns_empty(self) -> None:
        coordinator = _make_coordinator()
        assert await coordinator._propose_actions(()) == ()

    async def test_maps_known_pillars_to_default_actions(self) -> None:
        """Known pillars resolve to the source-of-truth default mapping.

        Tracks ``_DEFAULT_PATTERN_ACTIONS`` rather than hard-coding the
        action strings so edits to that mapping do not silently drift
        this test's expectation.
        """
        coordinator = _make_coordinator()
        actions = await coordinator._propose_actions(
            ("weakness:intelligence", "weakness:governance"),
        )
        assert actions == (
            _DEFAULT_PATTERN_ACTIONS["intelligence"],
            _DEFAULT_PATTERN_ACTIONS["governance"],
        )

    async def test_override_beats_default(self) -> None:
        config = EvalLoopConfig(
            pattern_action_map={"intelligence": "custom_action"},
        )
        coordinator = _make_coordinator(config=config)
        actions = await coordinator._propose_actions(
            ("weakness:intelligence",),
        )
        assert actions == ("custom_action",)

    async def test_unknown_pattern_skipped(self) -> None:
        import structlog

        from synthorg.observability.events.eval_loop import (
            EVAL_LOOP_ACTION_PROPOSED,
        )

        coordinator = _make_coordinator()
        with structlog.testing.capture_logs() as cap:
            actions = await coordinator._propose_actions(
                ("weakness:unknown",),
            )
        assert actions == ()
        # Assert the warning-path contract: an operator grepping the
        # logs for ``reason="unmapped_pattern"`` must find the event.
        unmapped = [
            rec
            for rec in cap
            if rec.get("event") == EVAL_LOOP_ACTION_PROPOSED
            and rec.get("reason") == "unmapped_pattern"
        ]
        assert len(unmapped) == 1, f"expected one unmapped_pattern warning; got {cap!r}"

    async def test_malformed_pattern_skipped(self) -> None:
        import structlog

        from synthorg.observability.events.eval_loop import (
            EVAL_LOOP_ACTION_PROPOSED,
        )

        coordinator = _make_coordinator()
        # No colon means unparseable; the coordinator logs a warning
        # (EVAL_LOOP_ACTION_PROPOSED with reason="malformed_pattern")
        # and skips the entry rather than emitting a bogus action.
        with structlog.testing.capture_logs() as cap:
            actions = await coordinator._propose_actions(
                ("justatoken",),
            )
        assert actions == ()
        malformed = [
            rec
            for rec in cap
            if rec.get("event") == EVAL_LOOP_ACTION_PROPOSED
            and rec.get("reason") == "malformed_pattern"
        ]
        assert len(malformed) == 1, (
            f"expected one malformed_pattern warning; got {cap!r}"
        )

    async def test_empty_pillar_after_colon_skipped(self) -> None:
        """``"weakness:"`` (colon but empty pillar) is treated as unmapped.

        The coordinator splits on ``":"`` so the pillar name ends up
        empty; neither the operator override nor
        ``_DEFAULT_PATTERN_ACTIONS`` has an entry keyed by ``""``.
        The expected behaviour is a WARNING-logged skip with
        ``reason="unmapped_pattern"`` -- there is no action for an
        empty pillar name and silently dropping it would hide
        configuration bugs.
        """
        import structlog

        from synthorg.observability.events.eval_loop import (
            EVAL_LOOP_ACTION_PROPOSED,
        )

        coordinator = _make_coordinator()
        with structlog.testing.capture_logs() as cap:
            actions = await coordinator._propose_actions(("weakness:",))
        assert actions == ()
        unmapped = [
            rec
            for rec in cap
            if rec.get("event") == EVAL_LOOP_ACTION_PROPOSED
            and rec.get("reason") == "unmapped_pattern"
        ]
        assert len(unmapped) == 1, f"expected one unmapped_pattern warning; got {cap!r}"
