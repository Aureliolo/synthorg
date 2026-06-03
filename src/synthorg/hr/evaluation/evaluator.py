"""Evaluation service -- five-pillar orchestrator.

Central service for computing five-pillar evaluation reports.
Delegates to a single pluggable ``PillarScoringStrategy`` per
pillar (defaulting to ``ConfigurablePillarScorer`` composed with a
per-pillar ``MetricExtractor``) and handles pillar toggling with
weight redistribution.
"""

import asyncio
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.hr.evaluation.constants import (
    MAX_SCORE,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.models import (
    EvaluationContext,
    EvaluationReport,
    InteractionFeedback,
    PillarScore,
    ResilienceMetrics,
    redistribute_weights,
)
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    LlmCalibrationRecord,
    TaskMetricRecord,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evaluation import (
    EVAL_FEEDBACK_RECORDED,
    EVAL_PILLAR_SKIPPED,
    EVAL_REPORT_COMPUTED,
    EVAL_WEIGHTS_REDISTRIBUTED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from synthorg.hr.evaluation.pillar_protocol import PillarScoringStrategy
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_MIN_QUALITY_SCORES_FOR_STDDEV: int = 2


class EvaluationService:
    """Central service for computing five-pillar evaluation reports.

    Delegates to one pluggable ``PillarScoringStrategy`` per pillar
    (Intelligence, Efficiency, Resilience, Governance, Experience).
    The default per-pillar strategy is ``ConfigurablePillarScorer``
    composed with the corresponding ``MetricExtractor`` (see
    ``hr/evaluation/extractors/``). Disabled pillars are skipped
    and their weight redistributed.

    Args:
        tracker: Performance tracker for snapshot and metric data.
        intelligence_strategy: Intelligence/Accuracy strategy (optional).
        efficiency_strategy: Performance/Efficiency strategy (optional).
        resilience_strategy: Reliability/Resilience strategy (optional).
        governance_strategy: Responsibility/Governance strategy (optional).
        ux_strategy: User Experience strategy (optional).
        config: Evaluation configuration (optional, defaults to all
            pillars enabled).
        config_resolver: Settings resolver that gates the four
            ``hr.evaluation_*_enabled`` runtime kill switches; passed
            through to the default ``EfficiencyMetricExtractor`` and
            consulted by ``_get_pillar_configs``.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        tracker: PerformanceTracker,
        intelligence_strategy: PillarScoringStrategy | None = None,
        efficiency_strategy: PillarScoringStrategy | None = None,
        resilience_strategy: PillarScoringStrategy | None = None,
        governance_strategy: PillarScoringStrategy | None = None,
        ux_strategy: PillarScoringStrategy | None = None,
        config: EvaluationConfig | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialize the evaluation service."""
        self._tracker = tracker
        self._config = config or EvaluationConfig()
        # Resolver gates the four hr.evaluation_*_enabled kill switches.
        # Mapping (best-effort, since the registry flags don't align
        # 1:1 with the pillar architecture):
        #   evaluation_quality_enabled    -> Intelligence pillar
        #   evaluation_task_count_enabled -> Resilience pillar
        #   evaluation_cost_enabled       -> efficiency cost sub-metric
        #   evaluation_latency_enabled    -> efficiency time sub-metric
        # Without a resolver wired we fall back to the YAML-baked
        # ``pillar.enabled`` / ``efficiency.cost_enabled`` /
        # ``efficiency.time_enabled`` fields so standalone construction
        # still honours the documented defaults.
        self._config_resolver = config_resolver
        self._intelligence = intelligence_strategy or self._default_intelligence()
        self._efficiency = efficiency_strategy or self._default_efficiency()
        self._resilience = resilience_strategy or self._default_resilience()
        self._governance = governance_strategy or self._default_governance()
        self._ux = ux_strategy or self._default_ux()
        self._feedback: dict[str, list[InteractionFeedback]] = {}

    @staticmethod
    def _default_intelligence() -> PillarScoringStrategy:
        """Default intelligence.

        Returns:
            Result of type ``PillarScoringStrategy``.
        """
        from synthorg.hr.evaluation.configurable_scorer import (  # noqa: PLC0415
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.intelligence import (  # noqa: PLC0415
            IntelligenceMetricExtractor,
        )

        return ConfigurablePillarScorer(
            EvaluationPillar.INTELLIGENCE,
            IntelligenceMetricExtractor(),
        )

    def _default_efficiency(self) -> PillarScoringStrategy:
        """Default efficiency.

        Returns:
            Result of type ``PillarScoringStrategy``.
        """
        from synthorg.hr.evaluation.configurable_scorer import (  # noqa: PLC0415
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.efficiency import (  # noqa: PLC0415
            EfficiencyMetricExtractor,
        )

        return ConfigurablePillarScorer(
            EvaluationPillar.EFFICIENCY,
            EfficiencyMetricExtractor(config_resolver=self._config_resolver),
        )

    @staticmethod
    def _default_resilience() -> PillarScoringStrategy:
        """Default resilience.

        Returns:
            Result of type ``PillarScoringStrategy``.
        """
        from synthorg.hr.evaluation.configurable_scorer import (  # noqa: PLC0415
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.resilience import (  # noqa: PLC0415
            ResilienceMetricExtractor,
        )

        return ConfigurablePillarScorer(
            EvaluationPillar.RESILIENCE,
            ResilienceMetricExtractor(),
        )

    @staticmethod
    def _default_governance() -> PillarScoringStrategy:
        """Default governance.

        Returns:
            Result of type ``PillarScoringStrategy``.
        """
        from synthorg.hr.evaluation.configurable_scorer import (  # noqa: PLC0415
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.governance import (  # noqa: PLC0415
            GovernanceMetricExtractor,
        )

        return ConfigurablePillarScorer(
            EvaluationPillar.GOVERNANCE,
            GovernanceMetricExtractor(),
        )

    @staticmethod
    def _default_ux() -> PillarScoringStrategy:
        """Default ux.

        Returns:
            Result of type ``PillarScoringStrategy``.
        """
        from synthorg.hr.evaluation.configurable_scorer import (  # noqa: PLC0415
            ConfigurablePillarScorer,
        )
        from synthorg.hr.evaluation.extractors.experience import (  # noqa: PLC0415
            ExperienceMetricExtractor,
        )

        return ConfigurablePillarScorer(
            EvaluationPillar.EXPERIENCE,
            ExperienceMetricExtractor(),
        )

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> EvaluationReport:
        """Compute a five-pillar evaluation report for an agent.

        Builds an evaluation context from the tracker, gathers stored
        feedback, computes resilience metrics from task records, then
        scores enabled pillars concurrently. Disabled pillars are
        skipped with their weight redistributed.

        Args:
            agent_id: Agent to evaluate.
            now: Reference time (defaults to current UTC time).

        Returns:
            Complete evaluation report with pillar scores.
        """
        if now is None:
            now = datetime.now(UTC)

        context = await self._build_context(agent_id, now=now)
        enabled, weights = await self._resolve_enabled_pillars(agent_id)
        pillar_scores = await self._score_pillars(enabled, context)
        return self._assemble_report(
            agent_id,
            now,
            context.snapshot,
            pillar_scores,
            weights,
        )

    async def _build_context(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime,
    ) -> EvaluationContext:
        """Fetch data from tracker and build the evaluation context.

        Returns:
            Result of type ``EvaluationContext``.
        """
        cfg = self._config
        snapshot = await self._tracker.get_snapshot(agent_id, now=now)
        task_records = self._tracker.get_task_metrics(agent_id=agent_id)

        calibration_records: tuple[LlmCalibrationRecord, ...] = ()
        if self._tracker.sampler is not None:
            calibration_records = self._tracker.sampler.get_calibration_records(
                agent_id=agent_id,
            )

        feedback = tuple(self._feedback.get(str(agent_id), []))
        resilience_metrics = self._compute_resilience_metrics(task_records)

        return EvaluationContext(
            agent_id=agent_id,
            now=now,
            config=cfg,
            snapshot=snapshot,
            task_records=task_records,
            calibration_records=calibration_records,
            feedback=feedback,
            resilience_metrics=resilience_metrics,
        )

    async def _get_pillar_configs(
        self,
    ) -> list[tuple[EvaluationPillar, bool, float, PillarScoringStrategy]]:
        """Return pillar configuration tuples.

        Consults the resolver for ``hr.evaluation_quality_enabled``
        (Intelligence) and ``hr.evaluation_task_count_enabled``
        (Resilience) when wired; falls back to the YAML-baked
        ``pillar.enabled`` field for the other pillars and when no
        resolver is wired.

        Returns:
            List of ``tuple[EvaluationPillar, bool, float, PillarScoringStrategy]``.
        """
        cfg = self._config
        # The two resolver lookups are independent; running them
        # concurrently in a ``TaskGroup`` keeps the per-evaluation
        # latency bounded by a single resolver round-trip rather than
        # two sequential ones.
        async with asyncio.TaskGroup() as tg:
            intelligence_task = tg.create_task(
                resolve_bool_with_fallback(
                    resolver=self._config_resolver,
                    namespace="hr",
                    key="evaluation_quality_enabled",
                    fallback=cfg.intelligence.enabled,
                ),
            )
            resilience_task = tg.create_task(
                resolve_bool_with_fallback(
                    resolver=self._config_resolver,
                    namespace="hr",
                    key="evaluation_task_count_enabled",
                    fallback=cfg.resilience.enabled,
                ),
            )
        intelligence_enabled = intelligence_task.result()
        resilience_enabled = resilience_task.result()
        return [
            (
                EvaluationPillar.INTELLIGENCE,
                intelligence_enabled,
                cfg.intelligence.weight,
                self._intelligence,
            ),
            (
                EvaluationPillar.EFFICIENCY,
                cfg.efficiency.enabled,
                cfg.efficiency.weight,
                self._efficiency,
            ),
            (
                EvaluationPillar.RESILIENCE,
                resilience_enabled,
                cfg.resilience.weight,
                self._resilience,
            ),
            (
                EvaluationPillar.GOVERNANCE,
                cfg.governance.enabled,
                cfg.governance.weight,
                self._governance,
            ),
            (
                EvaluationPillar.EXPERIENCE,
                cfg.experience.enabled,
                cfg.experience.weight,
                self._ux,
            ),
        ]

    async def _resolve_enabled_pillars(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[
        list[tuple[EvaluationPillar, PillarScoringStrategy]],
        dict[str, float],
    ]:
        """Determine enabled pillars, log skipped ones, redistribute weights.

        Returns:
            Tuple ``(list[tuple[EvaluationPillar, PillarScoringStrategy]], dict[str,
            float])``.
        """
        pillar_map = await self._get_pillar_configs()

        enabled: list[tuple[EvaluationPillar, float, PillarScoringStrategy]] = []
        for pillar, is_enabled, weight, strategy in pillar_map:
            if is_enabled:
                enabled.append((pillar, weight, strategy))
            else:
                logger.debug(
                    EVAL_PILLAR_SKIPPED,
                    agent_id=agent_id,
                    pillar=pillar.value,
                )

        weights = redistribute_weights(
            [(p.value, w, True) for p, w, _ in enabled],
        )
        logger.debug(
            EVAL_WEIGHTS_REDISTRIBUTED,
            agent_id=agent_id,
            weights=weights,
        )

        return [(p, s) for p, _w, s in enabled], weights

    async def _score_pillars(
        self,
        enabled: list[tuple[EvaluationPillar, PillarScoringStrategy]],
        context: EvaluationContext,
    ) -> list[PillarScore]:
        """Score all enabled pillars concurrently via TaskGroup.

        Returns:
            List of ``PillarScore``.
        """
        async with asyncio.TaskGroup() as tg:
            tasks: dict[EvaluationPillar, asyncio.Task[PillarScore]] = {
                pillar: tg.create_task(strategy.score(context=context))
                for pillar, strategy in enabled
            }
        return [tasks[p].result() for p, _ in enabled]

    def _assemble_report(
        self,
        agent_id: NotBlankStr,
        now: datetime,
        snapshot: AgentPerformanceSnapshot,
        pillar_scores: list[PillarScore],
        weights: dict[str, float],
    ) -> EvaluationReport:
        """Compute weighted overall score and build the report.

        Returns:
            Result of type ``EvaluationReport``.
        """
        overall_score = 0.0
        overall_confidence = 0.0
        for ps in pillar_scores:
            w = weights.get(ps.pillar.value, 0.0)
            overall_score += ps.score * w
            overall_confidence += ps.confidence * w

        overall_score = max(0.0, min(MAX_SCORE, overall_score))
        overall_confidence = max(0.0, min(1.0, overall_confidence))

        pillar_weights = tuple(
            (NotBlankStr(k), round(v, 6)) for k, v in sorted(weights.items())
        )

        report = EvaluationReport(
            agent_id=agent_id,
            computed_at=now,
            snapshot=snapshot,
            pillar_scores=tuple(pillar_scores),
            overall_score=round(overall_score, 4),
            overall_confidence=round(overall_confidence, 4),
            pillar_weights=pillar_weights,
        )

        logger.info(
            EVAL_REPORT_COMPUTED,
            agent_id=agent_id,
            pillar_count=len(pillar_scores),
            overall_score=report.overall_score,
            overall_confidence=report.overall_confidence,
        )
        return report

    def record_feedback(
        self,
        feedback: InteractionFeedback,
    ) -> InteractionFeedback:
        """Store interaction feedback for UX pillar scoring.

        Args:
            feedback: Interaction feedback to store.

        Returns:
            The stored feedback record.
        """
        agent_key = str(feedback.agent_id)
        self._feedback.setdefault(agent_key, []).append(feedback)

        logger.info(
            EVAL_FEEDBACK_RECORDED,
            agent_id=feedback.agent_id,
            source=feedback.source,
        )
        return feedback

    def get_feedback(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: datetime | None = None,
    ) -> tuple[InteractionFeedback, ...]:
        """Query stored feedback records.

        Args:
            agent_id: Filter by agent (None = all agents).
            since: Include records after this time.

        Returns:
            Matching feedback records.
        """
        if agent_id is not None:
            records = list(self._feedback.get(str(agent_id), []))
        else:
            records = [r for recs in self._feedback.values() for r in recs]

        if since is not None:
            records = [r for r in records if r.recorded_at >= since]
        return tuple(records)

    @staticmethod
    def _compute_resilience_metrics(
        records: tuple[TaskMetricRecord, ...],
    ) -> ResilienceMetrics:
        """Derive resilience metrics from raw task records.

        Sorts records by completion time, then computes success/failure
        counts, recovery rate, success streaks, and quality score
        standard deviation. Recovered tasks are capped at the failure
        count as a defensive invariant.

        Returns:
            Result of type ``ResilienceMetrics``.
        """
        total = len(records)
        if total == 0:
            return ResilienceMetrics(
                total_tasks=0,
                failed_tasks=0,
                recovered_tasks=0,
                current_success_streak=0,
                longest_success_streak=0,
            )

        sorted_records = sorted(records, key=lambda r: r.completed_at)
        failed, recovered, current_streak, longest_streak = _compute_streaks(
            sorted_records
        )
        stddev = _compute_quality_stddev(sorted_records)

        return ResilienceMetrics(
            total_tasks=total,
            failed_tasks=failed,
            recovered_tasks=min(recovered, failed),
            current_success_streak=current_streak,
            longest_success_streak=longest_streak,
            quality_score_stddev=stddev,
        )


def _compute_streaks(
    sorted_records: list[TaskMetricRecord],
) -> tuple[int, int, int, int]:
    """Compute failure count, recovery count, and streak stats.

    Returns:
        Tuple of (failed, recovered, current_streak, longest_streak).
    """
    failed = sum(1 for r in sorted_records if not r.is_success)
    recovered = 0
    current_streak = 0
    longest_streak = 0
    prev_failed = False

    for record in sorted_records:
        if record.is_success:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
            if prev_failed:
                recovered += 1
            prev_failed = False
        else:
            current_streak = 0
            prev_failed = True

    return failed, recovered, current_streak, longest_streak


def _compute_quality_stddev(
    sorted_records: list[TaskMetricRecord],
) -> float | None:
    """Compute population standard deviation of quality scores.

    Returns None when fewer than 2 scored records exist.

    Returns:
        The resulting ``float``, or ``None`` when unavailable.
    """
    quality_scores = [
        r.quality_score for r in sorted_records if r.quality_score is not None
    ]
    if len(quality_scores) < _MIN_QUALITY_SCORES_FOR_STDDEV:
        return None
    mean = sum(quality_scores) / len(quality_scores)
    variance = sum((s - mean) ** 2 for s in quality_scores) / len(
        quality_scores,
    )
    return round(math.sqrt(variance), 4)
