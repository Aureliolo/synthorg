"""Closed-loop evaluation coordinator.

Links existing services into the trace -> eval -> pattern -> fix
loop:

    trace capture (observability/)
      -> behavior tagging (BehaviorTaggerMiddleware)
        -> eval enrichment (EvaluationService + 5 pillars)
          -> pattern identification (EvaluationService analytics)
            -> targeted fix proposal (feeds TrainingService)
              -> validation (next run's trajectory scores)

``EvalLoopCoordinator`` does NOT implement any of these -- it
**orchestrates** the existing services into a single cycle.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.trajectory.scorer import TrajectoryScorer
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.deterministic_pattern_identifier import (
    DeterministicPatternIdentifier,
)
from synthorg.hr.evaluation.dogfooding_dataset_builder import (
    DogfoodingDatasetBuilder,
)
from synthorg.hr.evaluation.evaluator import EvaluationService
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkRunResult,
    EvalCycleReport,
)
from synthorg.hr.evaluation.external_benchmark_registry import (
    ExternalBenchmarkRegistry,
)
from synthorg.hr.evaluation.models import EvaluationReport
from synthorg.hr.evaluation.pattern_action_dispatcher import PatternActionDispatcher
from synthorg.hr.evaluation.pattern_protocols import (
    FixProposer,
    PatternIdentifier,
    ProposedAction,
)
from synthorg.hr.evaluation.table_fix_proposer import TableFixProposer
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.training.service import TrainingService
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_ACTION_DISPATCHED,
    EVAL_LOOP_AGENT_EVAL_FAILED,
    EVAL_LOOP_BENCHMARK_FAILED,
    EVAL_LOOP_CYCLE_COMPLETE,
    EVAL_LOOP_CYCLE_FAILED,
    EVAL_LOOP_CYCLE_START,
    EVAL_LOOP_TRAINING_TRIGGERED,
)

logger = get_logger(__name__)


class EvalLoopCoordinator:
    """Closed-loop evaluation coordinator.

    Orchestrates existing services into a single evaluation cycle:
    collect -> enrich -> identify -> propose -> validate.

    Args:
        performance_tracker: Source of task metrics and snapshots.
        evaluation_service: Five-pillar evaluation framework.
        trajectory_scorer: Best-of-K trajectory scorer.
        training_service: Training pipeline for targeted fixes.
        dataset_builder: Dogfooding dataset constructor.
        benchmark_registry: External benchmark registry.
        config: Coordinator configuration.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        performance_tracker: PerformanceTracker,
        evaluation_service: EvaluationService,
        trajectory_scorer: TrajectoryScorer,
        training_service: TrainingService,
        dataset_builder: DogfoodingDatasetBuilder,
        benchmark_registry: ExternalBenchmarkRegistry,
        config: EvalLoopConfig | None = None,
        action_dispatcher: PatternActionDispatcher | None = None,
        pattern_identifier: PatternIdentifier | None = None,
        fix_proposer: FixProposer | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._tracker = performance_tracker
        self._evaluation = evaluation_service
        self._scorer = trajectory_scorer
        self._training = training_service
        self._dataset_builder = dataset_builder
        self._benchmarks = benchmark_registry
        self._config = config or EvalLoopConfig()
        self._action_dispatcher = action_dispatcher
        # IDENTIFY / PROPOSE are pluggable; the deterministic threshold
        # identifier + static-table proposer are the shipped defaults
        # (and the fallback the provider-backed strategies degrade to).
        self._pattern_identifier: PatternIdentifier = (
            pattern_identifier or DeterministicPatternIdentifier(self._config)
        )
        self._fix_proposer: FixProposer = fix_proposer or TableFixProposer(self._config)
        self._clock: Clock = clock or SystemClock()

    def set_pattern_strategies(
        self,
        *,
        pattern_identifier: PatternIdentifier | None,
        fix_proposer: FixProposer | None,
    ) -> None:
        """Hot-swap the IDENTIFY / PROPOSE strategies.

        Pushed by ``EvalLoopSettingsSubscriber`` when an operator edits the
        ``hr.eval_loop_*`` model / mode keys, so a change applies on the next
        cycle without a restart. ``None`` resets the step to its deterministic
        default (the same fallback the constructor and the provider-backed
        strategies use). The next ``run_cycle`` reads these fresh.
        """
        self._pattern_identifier = pattern_identifier or DeterministicPatternIdentifier(
            self._config
        )
        self._fix_proposer = fix_proposer or TableFixProposer(self._config)

    @property
    def config(self) -> EvalLoopConfig:
        """Return the coordinator configuration."""
        return self._config

    async def run_cycle(
        self,
        *,
        window: timedelta,
        agent_ids: tuple[NotBlankStr, ...] | None = None,
    ) -> EvalCycleReport:
        """Execute one full evaluation cycle.

        Pipeline: collect -> enrich -> identify -> propose -> validate.

        Args:
            window: Time window to collect performance metrics from.
            agent_ids: Specific agents to evaluate (``None`` = all
                agents with metrics in the window).

        Returns:
            Complete cycle report with results.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        cycle_id = uuid4()
        now = self._clock.now()
        window_start = now - window
        start_time = self._clock.monotonic()

        logger.info(
            EVAL_LOOP_CYCLE_START,
            cycle_id=str(cycle_id),
            window_seconds=window.total_seconds(),
        )

        # Snapshot the pluggable strategies once for the whole cycle. A hot
        # ``set_pattern_strategies()`` swap (driven by an eval_loop settings
        # change) can fire between the IDENTIFY and PROPOSE awaits below;
        # reading the live attributes at each phase would otherwise run one
        # cycle with a mismatched (old identifier, new proposer) pair. The
        # reload takes effect on the next cycle instead.
        pattern_identifier = self._pattern_identifier
        fix_proposer = self._fix_proposer

        try:
            # 1. COLLECT: gather agent IDs with metrics in window.
            ids = agent_ids or self._collect_agent_ids(since=window_start)

            # 2. ENRICH: evaluate each agent via 5-pillar framework.
            reports = await self._enrich(ids)

            # 3. IDENTIFY: delegate to the pluggable pattern identifier,
            # honouring the global disable flag. The deterministic default
            # checks the flag internally, but an injected LLM identifier
            # would otherwise bypass it, so gate the phase here for both.
            observations: tuple[NotBlankStr, ...] = ()
            if self._config.pattern_identifier_enabled:
                observations = await pattern_identifier.identify(reports)

            # 4. PROPOSE: delegate to the pluggable fix proposer, then
            # dispatch the proposer's actual actions (deterministic table or
            # LLM) to their remediation service when a dispatcher is wired.
            proposed_actions = await fix_proposer.propose(observations)
            await self._dispatch_actions(proposed_actions)
            # The report records WHICH actions were proposed (provenance is an
            # internal dispatch concern), so flatten to action ids here.
            proposed_action_ids = tuple(pa.action_id for pa in proposed_actions)

            # 5. DECIDE: a cycle that identified corrective actions routes
            # them to the training pipeline -- gated so training (an
            # expensive action) never fires without an explicit opt-in.
            training_triggered = self._should_trigger_training(proposed_actions)
            if training_triggered:
                logger.info(
                    EVAL_LOOP_TRAINING_TRIGGERED,
                    cycle_id=str(cycle_id),
                    action_count=len(proposed_action_ids),
                    actions=list(proposed_action_ids),
                )

            # 6. Optionally run benchmarks.
            benchmark_results: tuple[BenchmarkRunResult, ...] = ()
            if self._config.benchmark_on_cycle:
                benchmark_results = await self._run_benchmarks()

            duration = self._clock.monotonic() - start_time

            report = EvalCycleReport(
                cycle_id=cycle_id,
                window_start=window_start,
                window_end=now,
                duration_seconds=duration,
                agents_evaluated=len(ids),
                agent_reports=reports,
                observations=observations,
                proposed_actions=proposed_action_ids,
                training_triggered=training_triggered,
                benchmark_results=benchmark_results,
                created_at=self._clock.now(),
            )

            logger.info(
                EVAL_LOOP_CYCLE_COMPLETE,
                cycle_id=str(cycle_id),
                agents_evaluated=len(ids),
                duration_seconds=duration,
            )

            return report  # noqa: TRY300

        except Exception as exc:
            log_exception_redacted(
                logger, EVAL_LOOP_CYCLE_FAILED, exc, cycle_id=str(cycle_id)
            )
            raise

    def _collect_agent_ids(
        self,
        *,
        since: datetime,
    ) -> tuple[NotBlankStr, ...]:
        """Collect unique agent IDs from recent task metrics.

        Returns:
            Tuple of ``NotBlankStr``.
        """
        records = self._tracker.get_task_metrics(since=since)
        seen: set[str] = set()
        ids: list[NotBlankStr] = []
        for record in records:
            if record.agent_id not in seen:
                seen.add(record.agent_id)
                ids.append(record.agent_id)
        return tuple(ids)

    async def _enrich(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[EvaluationReport, ...]:
        """Evaluate all agents concurrently via TaskGroup.

        Returns:
            Tuple of ``EvaluationReport``.
        """
        if not agent_ids:
            return ()

        reports: list[EvaluationReport] = []

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._evaluate_one(agent_id)) for agent_id in agent_ids
            ]

        for task in tasks:
            result = task.result()
            if result is not None:
                reports.append(result)

        return tuple(reports)

    async def _evaluate_one(
        self,
        agent_id: NotBlankStr,
    ) -> EvaluationReport | None:
        """Evaluate a single agent, isolating failures.

        Returns:
            The resulting ``EvaluationReport``, or ``None`` when unavailable.
        """
        try:
            return await self._evaluation.evaluate(agent_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, EVAL_LOOP_AGENT_EVAL_FAILED, exc, agent_id=agent_id
            )
            return None

    async def _dispatch_actions(
        self,
        proposed: tuple[ProposedAction, ...],
    ) -> None:
        """Route each proposed action to its remediation service.

        No-op when no dispatcher is wired (the loop keeps its propose + log
        behaviour). ``proposed`` is the fix proposer's already-deduplicated
        output (deterministic table or LLM), so each action is dispatched
        exactly once, with its OWN originating pattern(s) as the operator-alert
        context rather than the whole cycle's pattern set. A dispatcher failure
        is logged and the remaining actions still dispatch (criticals re-raise).

        Args:
            proposed: De-duplicated actions from the fix proposer, each
                carrying the weakness pattern(s) that produced it.
        """
        if self._action_dispatcher is None or not proposed:
            return
        for action_id, patterns in proposed:
            context = (
                NotBlankStr(", ".join(patterns)) if patterns else NotBlankStr("cycle")
            )
            try:
                accepted = await self._action_dispatcher.dispatch(action_id, context)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    EVAL_LOOP_ACTION_DISPATCHED,
                    action_id=action_id,
                    pattern=context,
                    dispatched=False,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            if accepted:
                logger.info(
                    EVAL_LOOP_ACTION_DISPATCHED,
                    action_id=action_id,
                    pattern=context,
                    dispatched=True,
                    accepted=True,
                )
            else:
                # No downstream handler claimed the action: it was dropped,
                # not dispatched. Logging dispatched=True here would turn a
                # silent drop into a success signal for operators/metrics.
                logger.warning(
                    EVAL_LOOP_ACTION_DISPATCHED,
                    action_id=action_id,
                    pattern=context,
                    dispatched=False,
                    accepted=False,
                )

    def _should_trigger_training(
        self,
        proposed_actions: tuple[ProposedAction, ...],
    ) -> bool:
        """Decide whether this cycle's actions route to the training pipeline.

        Training is an expensive, cost-incurring action, so it fires
        only when the cycle identified at least one corrective action
        *and* the operator opted in via ``config.training_on_actions``.

        Args:
            proposed_actions: Actions returned by :meth:`_propose_actions`.

        Returns:
            ``True`` when training should be triggered for this cycle.
        """
        return bool(proposed_actions) and self._config.training_on_actions

    async def _run_benchmarks(self) -> tuple[BenchmarkRunResult, ...]:
        """Run all registered benchmarks concurrently.

        Each benchmark is isolated: one failure does not cancel
        siblings (per CLAUDE.md TaskGroup convention for independent
        workers).

        Returns:
            Tuple of ``BenchmarkRunResult``.
        """
        names = self._benchmarks.list_registered()
        if not names:
            return ()

        max_concurrent = self._config.max_concurrent_benchmarks
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run_one(name: str) -> BenchmarkRunResult | None:
            """Run one.

            Returns:
                The resulting ``BenchmarkRunResult``, or ``None`` when unavailable.
            """
            try:
                async with semaphore:
                    return await self._benchmarks.run_benchmark(name)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(
                    logger, EVAL_LOOP_BENCHMARK_FAILED, exc, benchmark_name=name
                )
                return None

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_run_one(n)) for n in names]

        completed: list[BenchmarkRunResult] = []
        for task in tasks:
            result = task.result()
            if result is not None:
                completed.append(result)
        return tuple(completed)
