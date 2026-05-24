"""Closed-loop evaluation coordinator.

Links existing services into the trace -> eval -> pattern -> fix
loop:

    trace capture (observability/)
      -> behavior tagging (BehaviorTaggerMiddleware)
        -> eval enrichment (EvaluationService + 5 pillars)
          -> pattern identification (stub for v0.7)
            -> targeted fix proposal (feeds TrainingService)
              -> validation (next run's trajectory scores)

``EvalLoopCoordinator`` does NOT implement any of these -- it
**orchestrates** the existing services into a single cycle.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.types import NotBlankStr
from synthorg.engine.trajectory.scorer import TrajectoryScorer  # noqa: TC001
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.dogfooding_dataset_builder import (
    DogfoodingDatasetBuilder,  # noqa: TC001
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.evaluator import EvaluationService  # noqa: TC001
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkRunResult,
    EvalCycleReport,
)
from synthorg.hr.evaluation.external_benchmark_registry import (
    ExternalBenchmarkRegistry,  # noqa: TC001
)
from synthorg.hr.evaluation.models import EvaluationReport  # noqa: TC001
from synthorg.hr.performance.tracker import PerformanceTracker  # noqa: TC001
from synthorg.hr.training.service import TrainingService  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_ACTION_PROPOSED,
    EVAL_LOOP_AGENT_EVAL_FAILED,
    EVAL_LOOP_BENCHMARK_FAILED,
    EVAL_LOOP_CONFIG_DRIFT,
    EVAL_LOOP_CYCLE_COMPLETE,
    EVAL_LOOP_CYCLE_FAILED,
    EVAL_LOOP_CYCLE_START,
    EVAL_LOOP_PATTERN_IDENTIFIED,
)

logger = get_logger(__name__)

# Keys + values are both identifier fields -- type them as
# ``NotBlankStr`` so a future edit that leaves a blank / whitespace-
# only action id is rejected statically. The values in this literal
# mapping are non-blank by construction, but Pydantic validates
# them at config-load time when operators override the mapping via
# ``EvalLoopConfig.pattern_action_map`` (see that field).
# Keys are plain ``str`` to match ``EvalLoopConfig.pattern_action_map``
# (``dict[str, NotBlankStr]``): pattern keys come from
# ``EvaluationPillar.value`` (already non-blank by construction) and
# lookup sites ``.get(pillar)`` against both the default and the
# operator override, so the two containers must agree on key type.
# Values stay ``NotBlankStr`` so blank action ids are rejected
# statically + at Pydantic config-load time.
_DEFAULT_PATTERN_ACTIONS: Final[MappingProxyType[str, NotBlankStr]] = MappingProxyType(
    {
        EvaluationPillar.INTELLIGENCE.value: "increase_review_depth",
        EvaluationPillar.EFFICIENCY.value: "tighten_cost_budget",
        EvaluationPillar.RESILIENCE.value: "add_recovery_training",
        EvaluationPillar.GOVERNANCE.value: "expand_audit_coverage",
        EvaluationPillar.EXPERIENCE.value: "improve_tone_training",
    },
)

# Fail-fast drift guard: if a new ``EvaluationPillar`` is added but
# ``_DEFAULT_PATTERN_ACTIONS`` isn't updated, module import raises so
# ``_identify_patterns`` / ``_propose_actions`` can never run against
# an incomplete mapping (which would silently drop actions for the
# missing pillar). The check runs at import time (equivalent of a
# unit test) so the guard is exercised every time the module loads.
_EXPECTED_PATTERN_KEYS: Final[frozenset[str]] = frozenset(
    p.value for p in EvaluationPillar
)
if set(_DEFAULT_PATTERN_ACTIONS.keys()) != _EXPECTED_PATTERN_KEYS:
    _missing = _EXPECTED_PATTERN_KEYS - set(_DEFAULT_PATTERN_ACTIONS.keys())
    _extra = set(_DEFAULT_PATTERN_ACTIONS.keys()) - _EXPECTED_PATTERN_KEYS
    _msg = (
        "_DEFAULT_PATTERN_ACTIONS drifted from EvaluationPillar enum: "
        f"missing={sorted(_missing)!r}, extra={sorted(_extra)!r}"
    )
    # Log before raising so operators observing the structured log
    # stream see the drift details alongside whatever process-level
    # error surface the ``ImportError`` lands on (CI failure, import
    # crash at module load, etc.).
    logger.error(
        EVAL_LOOP_CONFIG_DRIFT,
        reason="default_pattern_actions_drift",
        missing=sorted(_missing),
        extra=sorted(_extra),
    )
    raise ImportError(_msg)

# Pattern kinds the ``_propose_actions`` mapper understands.  Any
# pattern whose prefix is not in this set is logged + skipped so a
# drifted detector cannot silently emit bogus actions via an unknown
# prefix (e.g. ``"strength:intelligence"``).
_SUPPORTED_PATTERN_KINDS: Final[frozenset[str]] = frozenset({"weakness"})


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
        clock: Clock | None = None,
    ) -> None:
        self._tracker = performance_tracker
        self._evaluation = evaluation_service
        self._scorer = trajectory_scorer
        self._training = training_service
        self._dataset_builder = dataset_builder
        self._benchmarks = benchmark_registry
        self._config = config or EvalLoopConfig()
        self._clock: Clock = clock or SystemClock()

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
        """
        cycle_id = NotBlankStr(str(uuid4()))
        now = datetime.now(UTC)
        window_start = now - window
        start_time = self._clock.monotonic()

        logger.info(
            EVAL_LOOP_CYCLE_START,
            cycle_id=cycle_id,
            window_seconds=window.total_seconds(),
        )

        try:
            # 1. COLLECT: gather agent IDs with metrics in window.
            ids = agent_ids or self._collect_agent_ids(since=window_start)

            # 2. ENRICH: evaluate each agent via 5-pillar framework.
            reports = await self._enrich(ids)

            # 3. IDENTIFY: pattern detection (stub).
            observations = await self._identify_patterns(reports)

            # 4. PROPOSE: action proposals (stub).
            proposed_actions = await self._propose_actions(observations)

            # 5. Optionally run benchmarks.
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
                proposed_actions=proposed_actions,
                training_triggered=False,
                benchmark_results=benchmark_results,
                created_at=datetime.now(UTC),
            )

            logger.info(
                EVAL_LOOP_CYCLE_COMPLETE,
                cycle_id=cycle_id,
                agents_evaluated=len(ids),
                duration_seconds=duration,
            )

            return report  # noqa: TRY300

        except Exception as exc:
            logger.error(
                EVAL_LOOP_CYCLE_FAILED,
                cycle_id=cycle_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    def _collect_agent_ids(
        self,
        *,
        since: datetime,
    ) -> tuple[NotBlankStr, ...]:
        """Collect unique agent IDs from recent task metrics."""
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
        """Evaluate all agents concurrently via TaskGroup."""
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
        """Evaluate a single agent, isolating failures."""
        try:
            return await self._evaluation.evaluate(agent_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                EVAL_LOOP_AGENT_EVAL_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _identify_patterns(
        self,
        reports: tuple[EvaluationReport, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Identify pillar-weakness patterns across agents.

        For each report, count pillars scoring below
        ``config.pattern_weakness_threshold``. Pillars with at least
        ``config.pattern_min_agents`` weak agents are returned as
        deterministic patterns ordered by weak-count (desc) then
        pillar name (asc) for stable output.

        Args:
            reports: Per-agent evaluation reports from the current cycle.

        Returns:
            Patterns in the form ``"weakness:<pillar>"``.
        """
        if not self._config.pattern_identifier_enabled or not reports:
            return ()

        threshold = self._config.pattern_weakness_threshold
        # Track unique weak agents per pillar (``pillar -> set[agent_id]``)
        # so the count reflects the number of distinct agents weak on a
        # pillar -- not the number of per-pillar score entries. This
        # protects against both (a) duplicate pillar entries within a
        # single report (defensive -- the model does not enforce
        # uniqueness) and (b) the same agent producing multiple reports
        # in the cycle window, which Counter-based arithmetic would
        # double-count.
        weak_agents_per_pillar: dict[str, set[str]] = {}
        for report in reports:
            weak_pillars = {
                score.pillar.value
                for score in report.pillar_scores
                if score.score < threshold
            }
            for pillar in weak_pillars:
                weak_agents_per_pillar.setdefault(pillar, set()).add(report.agent_id)

        min_agents = self._config.pattern_min_agents
        qualifying = [
            (pillar, len(agents))
            for pillar, agents in weak_agents_per_pillar.items()
            if len(agents) >= min_agents
        ]
        qualifying.sort(key=lambda item: (-item[1], item[0]))

        # ``NotBlankStr`` is ``Annotated[str, ...]`` -- it erases to
        # plain ``str`` at runtime and mypy considers the cast
        # redundant. The f-string is never empty since every
        # ``pillar`` comes from a non-empty ``EvaluationPillar.value``
        # constant, so the declared ``tuple[NotBlankStr, ...]``
        # return type is satisfied structurally.
        patterns = tuple(f"weakness:{pillar}" for pillar, _ in qualifying)
        if patterns:
            logger.info(
                EVAL_LOOP_PATTERN_IDENTIFIED,
                pattern_count=len(patterns),
                patterns=list(patterns),
                threshold=threshold,
                min_agents=min_agents,
            )
        return patterns

    async def _propose_actions(
        self,
        patterns: tuple[NotBlankStr, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Map identified patterns to action identifiers.

        Uses :data:`_DEFAULT_PATTERN_ACTIONS` keyed by
        :class:`EvaluationPillar` by default. Operators may override
        entries via ``config.pattern_action_map`` -- keys are pillar
        values, values are free-form action ids.

        Patterns are skipped (with a WARNING-level log via
        ``EVAL_LOOP_ACTION_PROPOSED``) in three cases:

        * ``reason="malformed_pattern"`` -- no ``:`` separator.
        * ``reason="unknown_pattern_kind"`` -- the prefix before the
          ``:`` is not in :data:`_SUPPORTED_PATTERN_KINDS` (e.g. a
          future detector emitting ``"strength:intelligence"`` is
          skipped until a mapping for that kind is wired).
        * ``reason="unmapped_pattern"`` -- neither the operator
          override nor :data:`_DEFAULT_PATTERN_ACTIONS` defines a
          mapping for the pillar.

        Operators chasing missing ``proposed_actions`` can grep the
        structured logs for those reasons to see which patterns were
        dropped and why.

        Args:
            patterns: Patterns returned by :meth:`_identify_patterns`.

        Returns:
            Ordered tuple of action identifiers.
        """
        if not patterns:
            return ()

        override = self._config.pattern_action_map or {}
        actions: list[NotBlankStr] = []
        for pattern in patterns:
            reason, mapped, extra = self._classify_pattern(pattern, override)
            if mapped is None:
                logger.warning(
                    EVAL_LOOP_ACTION_PROPOSED,
                    action_count=0,
                    reason=reason,
                    pattern=pattern,
                    **extra,
                )
                continue
            actions.append(mapped)

        # Two distinct weak pillars that share an action id (e.g. an
        # override collapsing two pillars onto ``"escalate_to_engineer"``)
        # must not fire the remediation twice.
        unique_actions = dedupe_preserving_order(actions)
        if unique_actions:
            logger.info(
                EVAL_LOOP_ACTION_PROPOSED,
                action_count=len(unique_actions),
                actions=list(unique_actions),
            )
        return unique_actions

    @staticmethod
    def _classify_pattern(
        pattern: str,
        override: dict[str, NotBlankStr],
    ) -> tuple[str, NotBlankStr | None, dict[str, str]]:
        """Map a pattern token to (reason, mapped_action, extra_log_fields).

        Returns ``mapped_action=None`` with a non-empty ``reason`` for
        every skip path (malformed / unknown kind / unmapped). The
        caller logs the WARNING once with ``reason`` + ``extra`` so
        ``_propose_actions`` stays under the 50-line ceiling without
        duplicating log-shape code.
        """
        if ":" not in pattern:
            return ("malformed_pattern", None, {})
        kind, pillar = pattern.split(":", 1)
        if kind not in _SUPPORTED_PATTERN_KINDS:
            return ("unknown_pattern_kind", None, {"kind": kind})
        mapped = override.get(pillar) or _DEFAULT_PATTERN_ACTIONS.get(pillar)
        if not mapped:
            return ("unmapped_pattern", None, {"pillar": pillar})
        return ("", mapped, {})

    async def _run_benchmarks(self) -> tuple[BenchmarkRunResult, ...]:
        """Run all registered benchmarks concurrently.

        Each benchmark is isolated: one failure does not cancel
        siblings (per CLAUDE.md TaskGroup convention for independent
        workers).
        """
        names = self._benchmarks.list_registered()
        if not names:
            return ()

        max_concurrent = self._config.max_concurrent_benchmarks
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run_one(name: str) -> BenchmarkRunResult | None:
            try:
                async with semaphore:
                    return await self._benchmarks.run_benchmark(name)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.error(
                    EVAL_LOOP_BENCHMARK_FAILED,
                    benchmark_name=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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
