# module-kind: complex_service
"""Runtime coordination metrics collection pipeline.

Collects raw data from live execution results, communication bus,
and baseline store; calls pure computation functions; logs results
via observability events; and fires alerts via NotificationDispatcher.

All collection is post-execution and never blocks the agent.
Individual metric failures are logged and skipped without blocking
remaining metric collection.
"""

from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from synthorg.budget.coordination_config import (
    CoordinationMetricName,
    CoordinationMetricsConfig,
    OrchestrationAlertThresholds,
)
from synthorg.budget.coordination_metrics import (
    AmdahlCeiling,
    CoordinationEfficiency,
    CoordinationMetrics,
    CoordinationOverhead,
    ErrorAmplification,
    MessageDensity,
    MessageOverhead,
    RedundancyRate,
    StragglerGap,
    TokenSpeedupRatio,
    compute_amdahl_ceiling,
    compute_efficiency,
    compute_error_amplification,
    compute_message_density,
    compute_message_overhead,
    compute_overhead,
    compute_redundancy_rate,
    compute_straggler_gap,
    compute_token_speedup_ratio,
)
from synthorg.budget.coordination_store import CoordinationMetricsRecord
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination_metrics import (
    COORD_METRICS_ALERT_FIRED,
    COORD_METRICS_COLLECTION_COMPLETED,
    COORD_METRICS_COLLECTION_FAILED,
    COORD_METRICS_COLLECTION_STARTED,
    COORD_METRICS_EFFICIENCY_COMPUTED,
    COORD_METRICS_ERROR_AMPLIFICATION_COMPUTED,
    COORD_METRICS_MESSAGE_DENSITY_COMPUTED,
    COORD_METRICS_OVERHEAD_COMPUTED,
    COORD_METRICS_REDUNDANCY_COMPUTED,
)
from synthorg.providers.enums import FinishReason

if TYPE_CHECKING:
    from synthorg.budget.baseline_store import BaselineStore
    from synthorg.budget.coordination_store import CoordinationMetricsStore
    from synthorg.budget.tracker import CostTracker
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.engine.loop_protocol import ExecutionResult
    from synthorg.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)

_MIN_TEAM_SIZE: int = 2


class CollectionInputs(NamedTuple):
    """Inputs for a single :meth:`CoordinationMetricsCollector.collect`.

    Bundles the post-execution context so the entry point takes one
    typed argument instead of eight keyword parameters.

    Attributes:
        execution_result: Completed execution result.
        agent_id: Executing agent identifier.
        task_id: Task identifier.
        team_size: Number of agents (1 for single-agent runs).
        agent_durations: Per-agent ``(agent_id, seconds)`` pairs.
        agent_outputs: Agent outputs for redundancy (multi-agent).
        is_multi_agent: Whether this is a multi-agent execution.
    """

    execution_result: ExecutionResult
    agent_id: str
    task_id: str
    team_size: int = 1
    agent_durations: tuple[tuple[str, float], ...] | None = None
    agent_outputs: tuple[str, ...] | None = None
    is_multi_agent: bool = False


def _extract_run_stats(
    execution_result: ExecutionResult,
) -> tuple[int, float, int]:
    """Extract basic run stats from an execution result.

    Returns:
        Tuple of (turns, error_rate, total_tokens).
    """
    turns = len(execution_result.turns)
    error_turns = sum(
        1
        for t in execution_result.turns
        if t.finish_reason in (FinishReason.ERROR, FinishReason.CONTENT_FILTER)
    )
    error_rate = error_turns / turns if turns > 0 else 0.0
    total_tokens = sum(t.total_tokens for t in execution_result.turns)
    return turns, error_rate, total_tokens


@runtime_checkable
class SimilarityComputer(Protocol):
    """Protocol for computing pairwise output similarity.

    Implementations compute the mean cosine (or other) similarity
    across agent output pairs for the redundancy rate metric (R).
    The embedding technology is intentionally left as TBD per the
    design spec -- this protocol decouples the collector from any
    specific embedding provider.
    """

    async def compute_pairwise_similarity(
        self,
        outputs: tuple[str, ...],
    ) -> tuple[float, ...]:
        """Compute pairwise similarity scores for agent outputs.

        Args:
            outputs: Agent output strings to compare.

        Returns:
            Sequence of similarity scores in [0.0, 1.0], one per
            unique pair (order: lexicographic pair enumeration).
        """
        ...


class CoordinationMetricsCollector:
    """Runtime collector for coordination metrics.

    Gathers raw data from execution results, CostTracker, and
    MessageBus; calls pure computation functions; logs events;
    dispatches alerts when overhead thresholds are exceeded.

    Collection is opt-in via ``config.enabled`` and
    ``config.collect``.  All operations are post-execution and
    never block the agent.

    Args:
        config: Coordination metrics configuration.
        cost_tracker: Cost tracker for turn/token data queries.
        message_bus: Optional message bus for message density (c).
            When ``None``, message_density is skipped.
        notification_dispatcher: Optional dispatcher for alerts.
            When ``None``, alerts are logged but not dispatched.
        similarity_computer: Optional protocol for redundancy rate
            (R) embedding computation. When ``None``, redundancy_rate
            is skipped.
        baseline_store: Optional store for single-agent baselines.
            When ``None``, efficiency, overhead, and error_amplification
            are skipped (no comparison data).
        metrics_store: Optional store the computed multi-agent metrics
            are recorded into. When ``None``, metrics are returned but
            not persisted (the ``/coordination/metrics`` API stays
            empty).
        clock: Clock seam for the record timestamp. Defaults to
            ``SystemClock``; tests inject ``FakeClock``.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: CoordinationMetricsConfig,
        cost_tracker: CostTracker,
        message_bus: MessageBus | None = None,
        notification_dispatcher: NotificationDispatcher | None = None,
        similarity_computer: SimilarityComputer | None = None,
        baseline_store: BaselineStore | None = None,
        metrics_store: CoordinationMetricsStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._cost_tracker = cost_tracker
        self._message_bus = message_bus
        self._notification_dispatcher = notification_dispatcher
        self._similarity_computer = similarity_computer
        self._baseline_store = baseline_store
        self._metrics_store = metrics_store
        self._clock: Clock = clock or SystemClock()

    def _is_enabled(self, metric: CoordinationMetricName) -> bool:
        """Return True if the metric is in config.collect."""
        return metric in self._config.collect

    async def collect(self, inputs: CollectionInputs) -> CoordinationMetrics:
        """Collect all enabled coordination metrics post-execution.

        Single-agent runs (``inputs.is_multi_agent=False``) only record
        baseline data and return an empty ``CoordinationMetrics``;
        multi-agent runs compute all enabled metrics against the
        accumulated baseline. Individual metric failures are logged and
        skipped without blocking the rest.

        Args:
            inputs: Post-execution collection context.

        Returns:
            Container of all collected metrics (None for skipped ones).
        """
        if not self._config.enabled:
            return CoordinationMetrics()

        logger.debug(
            COORD_METRICS_COLLECTION_STARTED,
            agent_id=inputs.agent_id,
            task_id=inputs.task_id,
            is_multi_agent=inputs.is_multi_agent,
            team_size=inputs.team_size,
        )

        turns, error_rate, total_tokens = _extract_run_stats(
            inputs.execution_result,
        )

        # Single-agent runs: record baseline (if store available), return early.
        if not inputs.is_multi_agent:
            self._record_baseline(
                inputs.agent_id,
                inputs.task_id,
                turns,
                error_rate,
                total_tokens,
                inputs.execution_result,
            )
            return CoordinationMetrics()

        # Multi-agent run: compute all enabled metrics.
        return await self._collect_multi_agent(
            turns=turns,
            error_rate=error_rate,
            total_tokens=total_tokens,
            agent_id=inputs.agent_id,
            task_id=inputs.task_id,
            team_size=inputs.team_size,
            agent_durations=inputs.agent_durations,
            agent_outputs=inputs.agent_outputs,
        )

    @staticmethod
    def _log_single_agent_completed(agent_id: str, task_id: str) -> None:
        """Emit the single-agent collection-completed debug event.

        Single-agent runs never compute metrics (they only feed the
        baseline), so ``metrics_computed`` is always 0.
        """
        logger.debug(
            COORD_METRICS_COLLECTION_COMPLETED,
            agent_id=agent_id,
            task_id=task_id,
            is_multi_agent=False,
            metrics_computed=0,
        )

    def _record_baseline(  # noqa: PLR0913
        self,
        agent_id: str,
        task_id: str,
        turns: int,
        error_rate: float,
        total_tokens: int,
        execution_result: ExecutionResult,
    ) -> None:
        """Record single-agent baseline data when store is available."""
        if self._baseline_store is None or turns == 0:
            self._log_single_agent_completed(agent_id, task_id)
            return

        from synthorg.budget.baseline_store import BaselineRecord  # noqa: PLC0415

        duration_seconds = (
            sum(t.latency_ms or 0.0 for t in execution_result.turns) / 1000.0
        )
        if duration_seconds <= 0:
            self._log_single_agent_completed(agent_id, task_id)
            return

        baseline = BaselineRecord(
            agent_id=agent_id,
            task_id=task_id,
            turns=float(turns),
            error_rate=error_rate,
            total_tokens=float(total_tokens),
            duration_seconds=duration_seconds,
        )
        self._baseline_store.record(baseline)
        self._log_single_agent_completed(agent_id, task_id)

    async def _collect_multi_agent(  # noqa: PLR0913
        self,
        *,
        turns: int,
        error_rate: float,
        total_tokens: int,
        agent_id: str,
        task_id: str,
        team_size: int,
        agent_durations: tuple[tuple[str, float], ...] | None,
        agent_outputs: tuple[str, ...] | None,
    ) -> CoordinationMetrics:
        """Compute all enabled metrics for a multi-agent execution."""
        metrics = await self._compute_all_metrics(
            turns=turns,
            error_rate=error_rate,
            total_tokens=total_tokens,
            team_size=team_size,
            agent_durations=agent_durations,
            agent_outputs=agent_outputs,
        )
        logger.info(
            COORD_METRICS_COLLECTION_COMPLETED,
            agent_id=agent_id,
            task_id=task_id,
            is_multi_agent=True,
            metrics_computed=self._count_computed(metrics),
        )
        await self._fire_alerts(metrics, agent_id=agent_id, task_id=task_id)
        self._record_metrics(task_id, team_size, metrics)
        return metrics

    async def _compute_all_metrics(  # noqa: PLR0913
        self,
        *,
        turns: int,
        error_rate: float,
        total_tokens: int,
        team_size: int,
        agent_durations: tuple[tuple[str, float], ...] | None,
        agent_outputs: tuple[str, ...] | None,
    ) -> CoordinationMetrics:
        """Run every enabled metric collector in order, assemble the result.

        The collectors are awaited in a fixed sequence (``message_overhead``
        consumes the already-computed ``message_density``); the order is
        load-bearing and must not be reshuffled.
        """
        efficiency = await self._try_collect_efficiency(turns, error_rate)
        overhead = await self._try_collect_overhead(turns)
        error_amplification = await self._try_collect_error_amplification(
            error_rate,
        )
        message_density = await self._try_collect_message_density(turns)
        redundancy_rate = await self._try_collect_redundancy(agent_outputs)
        amdahl_ceiling = await self._try_collect_amdahl(team_size)
        straggler_gap = await self._try_collect_straggler_gap(agent_durations)
        token_speedup = await self._try_collect_token_speedup(
            total_tokens,
            agent_durations,
        )
        message_overhead = await self._try_collect_message_overhead(
            team_size,
            message_density,
        )
        return CoordinationMetrics(
            efficiency=efficiency,
            overhead=overhead,
            error_amplification=error_amplification,
            message_density=message_density,
            redundancy_rate=redundancy_rate,
            amdahl_ceiling=amdahl_ceiling,
            straggler_gap=straggler_gap,
            token_speedup_ratio=token_speedup,
            message_overhead=message_overhead,
        )

    @staticmethod
    def _count_computed(metrics: CoordinationMetrics) -> int:
        """Number of metrics that were actually computed (non-``None``)."""
        return sum(
            1
            for m in (
                metrics.efficiency,
                metrics.overhead,
                metrics.error_amplification,
                metrics.message_density,
                metrics.redundancy_rate,
                metrics.amdahl_ceiling,
                metrics.straggler_gap,
                metrics.token_speedup_ratio,
                metrics.message_overhead,
            )
            if m is not None
        )

    def _record_metrics(
        self,
        task_id: str,
        team_size: int,
        metrics: CoordinationMetrics,
    ) -> None:
        """Persist multi-agent metrics into the store when one is wired.

        Multi-agent coordination is a system-level run with no single
        lead agent, so ``agent_id`` is ``None``.  Never fatal: a store
        write failure must not fail an already-completed run.
        """
        if self._metrics_store is None:
            return
        try:
            record = CoordinationMetricsRecord(
                task_id=task_id,
                agent_id=None,
                computed_at=self._clock.now(),
                team_size=team_size,
                metrics=metrics,
            )
            self._metrics_store.record(record)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="record_persist",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # Private collection helpers

    async def _try_collect_efficiency(
        self, turns_mas: int, error_rate: float
    ) -> CoordinationEfficiency | None:
        """Collect CoordinationEfficiency (Ec) if enabled and baseline available."""
        if not self._is_enabled(CoordinationMetricName.EFFICIENCY):
            return None
        if self._baseline_store is None:
            return None
        turns_sas = self._baseline_store.get_baseline_turns()
        if turns_sas is None:
            return None
        try:
            result = compute_efficiency(
                success_rate=1.0 - error_rate,
                turns_mas=max(float(turns_mas), 1.0),
                turns_sas=turns_sas,
            )
            logger.info(
                COORD_METRICS_EFFICIENCY_COMPUTED,
                value=result.value,
                turns_mas=turns_mas,
                turns_sas=turns_sas,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="efficiency",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        else:
            return result

    async def _try_collect_overhead(
        self, turns_mas: int
    ) -> CoordinationOverhead | None:
        """Collect CoordinationOverhead (O%) if enabled and baseline available."""
        if not self._is_enabled(CoordinationMetricName.OVERHEAD):
            return None
        if self._baseline_store is None:
            return None
        turns_sas = self._baseline_store.get_baseline_turns()
        if turns_sas is None:
            return None
        try:
            result = compute_overhead(
                turns_mas=max(float(turns_mas), 1.0),
                turns_sas=turns_sas,
            )
            logger.info(
                COORD_METRICS_OVERHEAD_COMPUTED,
                value_percent=result.value_percent,
                turns_mas=turns_mas,
                turns_sas=turns_sas,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="overhead",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        else:
            return result

    async def _try_collect_error_amplification(
        self, error_rate_mas: float
    ) -> ErrorAmplification | None:
        """Collect ErrorAmplification (Ae) if enabled and baseline available."""
        if not self._is_enabled(CoordinationMetricName.ERROR_AMPLIFICATION):
            return None
        if self._baseline_store is None:
            return None
        error_rate_sas = self._baseline_store.get_baseline_error_rate()
        if error_rate_sas is None:
            return None
        # Cannot compute Ae when SAS baseline is 0 (no errors in baseline)
        if error_rate_sas <= 0:
            return None
        try:
            result = compute_error_amplification(
                error_rate_mas=error_rate_mas,
                error_rate_sas=error_rate_sas,
            )
            logger.info(
                COORD_METRICS_ERROR_AMPLIFICATION_COMPUTED,
                value=result.value,
                error_rate_mas=error_rate_mas,
                error_rate_sas=error_rate_sas,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="error_amplification",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        else:
            return result

    async def _try_collect_message_density(
        self, reasoning_turns: int
    ) -> MessageDensity | None:
        """Collect MessageDensity (c) if enabled and message bus available."""
        if not self._is_enabled(CoordinationMetricName.MESSAGE_DENSITY):
            return None
        if self._message_bus is None:
            return None
        if reasoning_turns <= 0:
            return None
        try:
            channels = await self._message_bus.list_channels()
            inter_agent_messages = 0
            for channel in channels:
                history = await self._message_bus.get_channel_history(
                    channel.name,
                )
                inter_agent_messages += len(history)
            result = compute_message_density(
                inter_agent_messages=inter_agent_messages,
                reasoning_turns=reasoning_turns,
            )
            logger.info(
                COORD_METRICS_MESSAGE_DENSITY_COMPUTED,
                value=result.value,
                inter_agent_messages=inter_agent_messages,
                reasoning_turns=reasoning_turns,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="message_density",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        else:
            return result

    async def _try_collect_redundancy(
        self, agent_outputs: tuple[str, ...] | None
    ) -> RedundancyRate | None:
        """Collect RedundancyRate (R) if enabled and similarity computer available."""
        if not self._is_enabled(CoordinationMetricName.REDUNDANCY):
            return None
        if self._similarity_computer is None:
            return None
        if not agent_outputs or len(agent_outputs) < _MIN_TEAM_SIZE:
            return None
        try:
            similarities = await self._similarity_computer.compute_pairwise_similarity(
                agent_outputs
            )
            if not similarities:
                return None
            result = compute_redundancy_rate(similarities=list(similarities))
            logger.info(
                COORD_METRICS_REDUNDANCY_COMPUTED,
                value=result.value,
                sample_count=result.sample_count,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="redundancy",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        else:
            return result

    async def _try_collect_amdahl(self, team_size: int) -> AmdahlCeiling | None:
        """Collect AmdahlCeiling if enabled and team_size > 1."""
        if not self._is_enabled(CoordinationMetricName.AMDAHL_CEILING):
            return None
        if team_size < _MIN_TEAM_SIZE:
            return None
        try:
            # Estimate parallelizable fraction from team size: p = (n-1)/n
            p = (team_size - 1) / team_size
            return compute_amdahl_ceiling(parallelizable_fraction=p)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="amdahl_ceiling",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _try_collect_straggler_gap(
        self, agent_durations: tuple[tuple[str, float], ...] | None
    ) -> StragglerGap | None:
        """Collect StragglerGap if enabled and agent_durations provided."""
        if not self._is_enabled(CoordinationMetricName.STRAGGLER_GAP):
            return None
        if not agent_durations or len(agent_durations) < _MIN_TEAM_SIZE:
            return None
        try:
            return compute_straggler_gap(agent_durations=list(agent_durations))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="straggler_gap",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _try_collect_token_speedup(
        self,
        total_tokens_mas: int,
        agent_durations: tuple[tuple[str, float], ...] | None,
    ) -> TokenSpeedupRatio | None:
        """Collect TokenSpeedupRatio if enabled and baseline/duration available."""
        if not self._is_enabled(CoordinationMetricName.TOKEN_SPEEDUP_RATIO):
            return None
        if self._baseline_store is None or not agent_durations:
            return None
        tokens_sas = self._baseline_store.get_baseline_tokens()
        duration_sas = self._baseline_store.get_baseline_duration()
        if tokens_sas is None or duration_sas is None:
            return None
        if tokens_sas <= 0 or duration_sas <= 0:
            return None
        duration_mas = sum(d for _, d in agent_durations)
        if duration_mas <= 0:
            return None
        try:
            return compute_token_speedup_ratio(
                tokens_mas=max(float(total_tokens_mas), 1.0),
                tokens_sas=tokens_sas,
                duration_mas=duration_mas,
                duration_sas=duration_sas,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="token_speedup_ratio",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _try_collect_message_overhead(
        self,
        team_size: int,
        message_density: MessageDensity | None,
    ) -> MessageOverhead | None:
        """Collect MessageOverhead if enabled and message_density was computed."""
        if not self._is_enabled(CoordinationMetricName.MESSAGE_OVERHEAD):
            return None
        if message_density is None:
            return None
        try:
            return compute_message_overhead(
                team_size=team_size,
                message_count=message_density.inter_agent_messages,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="message_overhead",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _fire_alerts(
        self,
        metrics: CoordinationMetrics,
        *,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Fire notifications for coordination overhead threshold crossings.

        When ``notification_dispatcher`` is ``None``, no alerts are fired.
        """
        dispatcher = self._notification_dispatcher
        if dispatcher is None:
            return
        overhead = metrics.overhead
        if overhead is None:
            return
        # O% is in percent; thresholds are fractions -> convert O% to fraction
        severity = self._classify_overhead_severity(
            overhead.value_percent / 100.0,
        )
        if severity is None:
            return
        await self._dispatch_overhead_alert(
            dispatcher,
            severity=severity,
            overhead=overhead,
            agent_id=agent_id,
            task_id=task_id,
        )

    def _classify_overhead_severity(self, overhead_fraction: float) -> str | None:
        """Map an overhead fraction to an alert severity.

        Returns ``None`` when the overhead is below the info threshold
        (no alert is fired in that case).
        """
        thresholds: OrchestrationAlertThresholds = self._config.orchestration_alerts
        if overhead_fraction >= thresholds.critical:
            return "critical"
        if overhead_fraction >= thresholds.warn:
            return "warning"
        if overhead_fraction >= thresholds.info:
            return "info"
        return None

    async def _dispatch_overhead_alert(
        self,
        dispatcher: NotificationDispatcher,
        *,
        severity: str,
        overhead: CoordinationOverhead,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Build and dispatch the overhead notification; never fatal."""
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        body = (
            f"Coordination overhead is {overhead.value_percent:.1f}% "
            f"({overhead.turns_mas:.0f} MAS turns vs "
            f"{overhead.turns_sas:.0f} SAS turns); "
            f"agent={agent_id}, task={task_id}"
        )
        try:
            await dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.BUDGET,
                    severity=NotificationSeverity(severity),
                    title="Coordination overhead alert",
                    body=body,
                    source="budget.coordination_collector",
                ),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                COORD_METRICS_COLLECTION_FAILED,
                metric="alert_dispatch",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return

        logger.warning(
            COORD_METRICS_ALERT_FIRED,
            agent_id=agent_id,
            task_id=task_id,
            severity=severity,
            overhead_percent=overhead.value_percent,
        )
