"""CFO cost optimization service.

Provides spending anomaly detection, cost efficiency analysis, model
downgrade recommendations, routing optimization suggestions, and
operation approval decisions.  Composes
:class:`~synthorg.budget.tracker.CostTracker` and
:class:`~synthorg.budget.config.BudgetConfig` for read-only analytical
queries — the advisory complement to
:class:`~synthorg.budget.enforcer.BudgetEnforcer`.

Service layer backing the CFO role (see Operations design page).
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.budget._optimizer_helpers import (
    _build_downgrade_recommendation,
    _build_efficiency_from_records,
    _classify_severity,
    _compute_alert_level,
    _compute_window_costs,
    _detect_spike_anomaly,
    _find_most_used_model,
    _group_records_by_agent,
)
from synthorg.budget.billing import billing_period_start
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.optimizer_models import (
    AnomalyDetectionResult,
    ApprovalDecision,
    CostOptimizerConfig,
    DowngradeAnalysis,
    DowngradeRecommendation,
    EfficiencyAnalysis,
    EfficiencyRating,
    RoutingOptimizationAnalysis,
    RoutingSuggestion,
    SpendingAnomaly,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger
from synthorg.observability.events.cfo import (
    CFO_ANOMALY_DETECTED,
    CFO_ANOMALY_SCAN_COMPLETE,
    CFO_APPROVAL_EVALUATED,
    CFO_DOWNGRADE_RECOMMENDED,
    CFO_DOWNGRADE_SKIPPED,
    CFO_EFFICIENCY_ANALYSIS_COMPLETE,
    CFO_OPERATION_DENIED,
    CFO_OPTIMIZER_CREATED,
    CFO_RESOLVER_MISSING,
    CFO_ROUTING_OPTIMIZATION_COMPLETE,
)

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.cost_record import CostRecord
    from synthorg.budget.tracker import CostTracker
    from synthorg.providers.routing.models import ResolvedModel
    from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)

# Same ordering as BudgetEnforcer._ALERT_LEVEL_ORDER
_ALERT_LEVEL_ORDER: dict[BudgetAlertLevel, int] = {
    BudgetAlertLevel.NORMAL: 0,
    BudgetAlertLevel.WARNING: 1,
    BudgetAlertLevel.CRITICAL: 2,
    BudgetAlertLevel.HARD_STOP: 3,
}

# Maximum number of time windows for anomaly detection to avoid
# excessive memory/compute from pathological inputs.
_MAX_WINDOW_COUNT = 1000


class CostOptimizer:
    """CFO analytical service for cost optimization.

    Composes CostTracker and BudgetConfig for read-only analysis:
    anomaly detection, efficiency analysis, downgrade recommendations,
    routing optimization suggestions, and operation approval evaluation.

    Args:
        cost_tracker: Cost tracking service for querying spend.
        budget_config: Budget configuration for limits and thresholds.
        config: Optimizer-specific configuration. Defaults to
            ``CostOptimizerConfig()`` when ``None``.
        model_resolver: Optional model resolver for downgrade and
            routing optimization recommendations.
    """

    def __init__(
        self,
        *,
        cost_tracker: CostTracker,
        budget_config: BudgetConfig,
        config: CostOptimizerConfig | None = None,
        model_resolver: ModelResolver | None = None,
    ) -> None:
        self._cost_tracker = cost_tracker
        self._budget_config = budget_config
        self._config = config or CostOptimizerConfig()
        self._model_resolver = model_resolver
        logger.debug(
            CFO_OPTIMIZER_CREATED,
            has_model_resolver=model_resolver is not None,
            anomaly_sigma=self._config.anomaly_sigma_threshold,
        )

    async def detect_anomalies(
        self,
        *,
        start: datetime,
        end: datetime,
        window_count: int = 5,
    ) -> AnomalyDetectionResult:
        """Detect spending anomalies in the given period.

        Divides ``[start, end)`` into ``window_count`` equal windows,
        groups records by agent, and flags agents whose last-window
        spending deviates significantly from their historical mean.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.
            window_count: Number of time windows to divide the period
                into.  Must be >= 2 and <= 1000.

        Returns:
            Anomaly detection result with any detected anomalies.

        Raises:
            ValueError: If ``start >= end``, ``window_count < 2``, or
                ``window_count > 1000``.
        """
        if start >= end:
            logger.warning(
                CFO_ANOMALY_SCAN_COMPLETE,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)
        if window_count < 2:  # noqa: PLR2004
            logger.warning(
                CFO_ANOMALY_SCAN_COMPLETE,
                error="window_count_below_minimum",
                window_count=window_count,
            )
            msg = f"window_count must be >= 2, got {window_count}"
            raise ValueError(msg)
        if window_count > _MAX_WINDOW_COUNT:
            logger.warning(
                CFO_ANOMALY_SCAN_COMPLETE,
                error="window_count_above_maximum",
                window_count=window_count,
            )
            msg = f"window_count must be <= {_MAX_WINDOW_COUNT}, got {window_count}"
            raise ValueError(msg)

        now = datetime.now(UTC)
        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )

        total_duration = end - start
        window_duration = total_duration / window_count
        window_starts = tuple(start + window_duration * i for i in range(window_count))

        # Pre-group records by agent for O(N+M) complexity (#8)
        by_agent = _group_records_by_agent(records)
        agent_ids = sorted(by_agent)
        anomalies: list[SpendingAnomaly] = []

        for agent_id in agent_ids:
            window_costs = _compute_window_costs(
                by_agent[agent_id],
                window_starts,
                window_duration,
            )
            anomaly = _detect_spike_anomaly(
                agent_id,
                window_costs,
                now,
                window_starts,
                window_duration,
                self._config,
            )
            if anomaly is not None:
                logger.warning(
                    CFO_ANOMALY_DETECTED,
                    agent_id=agent_id,
                    anomaly_type=anomaly.anomaly_type.value,
                    severity=anomaly.severity.value,
                    deviation_factor=anomaly.deviation_factor,
                )
                anomalies.append(anomaly)

        result = AnomalyDetectionResult(
            anomalies=tuple(anomalies),
            scan_period_start=start,
            scan_period_end=end,
            agents_scanned=len(agent_ids),
            scan_timestamp=now,
        )

        logger.info(
            CFO_ANOMALY_SCAN_COMPLETE,
            anomaly_count=len(anomalies),
            agents_scanned=len(agent_ids),
        )

        return result

    async def analyze_efficiency(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> EfficiencyAnalysis:
        """Analyze cost efficiency of all agents in the period.

        Computes cost-per-1k-tokens for each agent and rates them
        relative to the global average.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.

        Returns:
            Efficiency analysis with per-agent ratings.

        Raises:
            ValueError: If ``start >= end``.
        """
        if start >= end:
            logger.warning(
                CFO_EFFICIENCY_ANALYSIS_COMPLETE,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)

        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )

        result = _build_efficiency_from_records(
            records,
            start=start,
            end=end,
            threshold_factor=self._config.inefficiency_threshold_factor,
        )

        logger.info(
            CFO_EFFICIENCY_ANALYSIS_COMPLETE,
            agent_count=len(result.agents),
            inefficient_count=result.inefficient_agent_count,
            global_avg_cost_per_1k=result.global_avg_cost_per_1k,
        )

        return result

    async def recommend_downgrades(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> DowngradeAnalysis:
        """Recommend model downgrades for inefficient agents.

        Runs efficiency analysis and uses the model resolver and
        downgrade map to find cheaper alternatives.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.

        Returns:
            Downgrade analysis with recommendations. Empty when no
            model_resolver is configured.

        Raises:
            ValueError: If ``start >= end``.
        """
        if start >= end:
            logger.warning(
                CFO_DOWNGRADE_RECOMMENDED,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)

        if self._model_resolver is None:
            logger.warning(
                CFO_RESOLVER_MISSING,
                reason="no_model_resolver_configured",
            )
            budget_pressure = await self._compute_budget_pressure()
            return DowngradeAnalysis(
                recommendations=(),
                budget_pressure_percent=budget_pressure,
            )

        async with asyncio.TaskGroup() as tg:
            records_task = tg.create_task(
                self._cost_tracker.get_records(start=start, end=end),
            )
            pressure_task = tg.create_task(self._compute_budget_pressure())

        records = records_task.result()
        budget_pressure = pressure_task.result()

        efficiency = _build_efficiency_from_records(
            records,
            start=start,
            end=end,
            threshold_factor=self._config.inefficiency_threshold_factor,
        )

        logger.info(
            CFO_EFFICIENCY_ANALYSIS_COMPLETE,
            agent_count=len(efficiency.agents),
            inefficient_count=efficiency.inefficient_agent_count,
            global_avg_cost_per_1k=efficiency.global_avg_cost_per_1k,
        )

        by_agent = _group_records_by_agent(records)
        recommendations = self._build_recommendations(
            efficiency=efficiency,
            by_agent=by_agent,
        )

        return DowngradeAnalysis(
            recommendations=tuple(recommendations),
            budget_pressure_percent=budget_pressure,
        )

    async def suggest_routing_optimizations(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> RoutingOptimizationAnalysis:
        """Suggest routing optimizations based on actual usage patterns.

        Analyzes each agent's most-used model and suggests cheaper
        alternatives available through the model resolver, comparing by
        cost and context window size.

        Unlike ``recommend_downgrades`` which only targets INEFFICIENT
        agents, this method analyzes all agents and suggests cheaper
        alternatives regardless of efficiency rating — any agent that
        could use a cheaper model is a candidate.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.

        Returns:
            Routing optimization analysis with per-agent suggestions.
            Empty when no model_resolver is configured.

        Raises:
            ValueError: If ``start >= end``.
        """
        if start >= end:
            logger.warning(
                CFO_ROUTING_OPTIMIZATION_COMPLETE,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)

        if self._model_resolver is None:
            logger.warning(
                CFO_RESOLVER_MISSING,
                reason="no_model_resolver_configured",
            )
            return RoutingOptimizationAnalysis(
                suggestions=(),
                analysis_period_start=start,
                analysis_period_end=end,
                agents_analyzed=0,
            )

        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )

        by_agent = _group_records_by_agent(records)
        all_models = self._model_resolver.all_models_sorted_by_cost()
        suggestions = self._find_routing_suggestions(by_agent, all_models)

        result = RoutingOptimizationAnalysis(
            suggestions=tuple(suggestions),
            analysis_period_start=start,
            analysis_period_end=end,
            agents_analyzed=len(by_agent),
        )

        logger.info(
            CFO_ROUTING_OPTIMIZATION_COMPLETE,
            suggestion_count=len(suggestions),
            agents_analyzed=len(by_agent),
            total_savings_per_1k=result.total_estimated_savings_per_1k,
        )

        return result

    async def evaluate_operation(
        self,
        *,
        agent_id: str,
        estimated_cost_usd: float,
        now: datetime | None = None,
    ) -> ApprovalDecision:
        """Evaluate whether an operation should proceed.

        Evaluates three criteria in order:

        1. Rejects negative ``estimated_cost_usd`` immediately.
        2. Denies if the *projected* alert level (after adding the
           estimated cost) meets or exceeds the auto-deny threshold.
        3. Denies if the projected cost would exceed the hard-stop
           limit.
        4. Approves with optional warning conditions for high-cost
           operations or elevated alert levels.

        When ``total_monthly <= 0`` budget enforcement is disabled and
        the operation is always approved with no conditions.

        Args:
            agent_id: Agent requesting the operation.
            estimated_cost_usd: Estimated cost of the operation.  Must
                be >= 0.
            now: Reference timestamp for billing period computation.
                Defaults to ``datetime.now(UTC)``.

        Returns:
            Approval decision with reasoning.

        Raises:
            ValueError: If ``estimated_cost_usd`` is negative.
        """
        if estimated_cost_usd < 0:
            logger.warning(
                CFO_OPERATION_DENIED,
                agent_id=agent_id,
                estimated_cost=estimated_cost_usd,
                reason="negative_estimated_cost",
            )
            msg = f"estimated_cost_usd must be >= 0, got {estimated_cost_usd}"
            raise ValueError(msg)

        cfg = self._budget_config

        if cfg.total_monthly <= 0:
            return ApprovalDecision(
                approved=True,
                reason="Budget enforcement disabled (no monthly budget)",
                budget_remaining_usd=0.0,
                budget_used_percent=0.0,
                alert_level=BudgetAlertLevel.NORMAL,
                conditions=(),
            )

        period_start = billing_period_start(cfg.reset_day, now=now)
        monthly_cost = await self._cost_tracker.get_total_cost(
            start=period_start,
        )
        remaining = round(
            cfg.total_monthly - monthly_cost,
            BUDGET_ROUNDING_PRECISION,
        )
        used_pct = round(
            monthly_cost / cfg.total_monthly * 100,
            BUDGET_ROUNDING_PRECISION,
        )
        alert_level = _compute_alert_level(used_pct, cfg)

        # Use projected alert level (after cost) for auto-deny check (#11)
        projected_cost = round(
            monthly_cost + estimated_cost_usd,
            BUDGET_ROUNDING_PRECISION,
        )
        projected_pct = round(
            projected_cost / cfg.total_monthly * 100,
            BUDGET_ROUNDING_PRECISION,
        )
        projected_alert = _compute_alert_level(projected_pct, cfg)

        denial = self._check_denial(
            agent_id=agent_id,
            estimated_cost_usd=estimated_cost_usd,
            remaining=remaining,
            used_pct=used_pct,
            alert_level=alert_level,
            projected_cost=projected_cost,
            projected_alert=projected_alert,
        )
        if denial is not None:
            return denial

        conditions = self._build_approval_conditions(
            estimated_cost_usd=estimated_cost_usd,
            projected_alert=projected_alert,
            projected_pct=projected_pct,
        )

        decision = ApprovalDecision(
            approved=True,
            reason="Approved",
            budget_remaining_usd=remaining,
            budget_used_percent=used_pct,
            alert_level=alert_level,
            conditions=conditions,
        )

        logger.info(
            CFO_APPROVAL_EVALUATED,
            agent_id=agent_id,
            approved=True,
            estimated_cost=estimated_cost_usd,
            alert_level=alert_level.value,
            conditions_count=len(conditions),
        )

        return decision

    # ── Private helpers ──────────────────────────────────────────

    def _build_recommendations(
        self,
        *,
        efficiency: EfficiencyAnalysis,
        by_agent: dict[str, list[CostRecord]],
    ) -> list[DowngradeRecommendation]:
        """Build downgrade recommendations for inefficient agents."""
        assert self._model_resolver is not None  # noqa: S101
        downgrade_map = dict(self._budget_config.auto_downgrade.downgrade_map)
        recommendations: list[DowngradeRecommendation] = []

        for agent in efficiency.agents:
            if agent.efficiency_rating != EfficiencyRating.INEFFICIENT:
                continue

            agent_records = by_agent.get(agent.agent_id, [])
            most_used_model = _find_most_used_model(agent_records)
            if most_used_model is None:
                logger.debug(
                    CFO_DOWNGRADE_SKIPPED,
                    agent_id=agent.agent_id,
                    reason="no_most_used_model",
                )
                continue

            recommendation = _build_downgrade_recommendation(
                agent_id=agent.agent_id,
                current_model=most_used_model,
                downgrade_map=downgrade_map,
                resolver=self._model_resolver,
            )
            if recommendation is not None:
                recommendations.append(recommendation)
                logger.info(
                    CFO_DOWNGRADE_RECOMMENDED,
                    agent_id=agent.agent_id,
                    current_model=most_used_model,
                    recommended_model=recommendation.recommended_model,
                    estimated_savings=recommendation.estimated_savings_per_1k,
                )

        return recommendations

    def _find_routing_suggestions(
        self,
        by_agent: dict[str, list[CostRecord]],
        all_models: tuple[ResolvedModel, ...],
    ) -> list[RoutingSuggestion]:
        """Find routing suggestions for all agents."""
        assert self._model_resolver is not None  # noqa: S101
        suggestions: list[RoutingSuggestion] = []

        for agent_id in sorted(by_agent):
            agent_records = by_agent[agent_id]
            most_used = _find_most_used_model(agent_records)
            if most_used is None:
                continue

            current_resolved = self._model_resolver.resolve_safe(most_used)
            if current_resolved is None:
                continue

            # Find cheapest model with sufficient context window
            for candidate in all_models:
                if candidate.model_id == current_resolved.model_id:
                    continue
                if candidate.total_cost_per_1k >= current_resolved.total_cost_per_1k:
                    continue
                if candidate.max_context < current_resolved.max_context:
                    continue

                suggestions.append(
                    RoutingSuggestion(
                        agent_id=agent_id,
                        current_model=most_used,
                        suggested_model=candidate.model_id,
                        current_cost_per_1k=round(
                            current_resolved.total_cost_per_1k,
                            BUDGET_ROUNDING_PRECISION,
                        ),
                        suggested_cost_per_1k=round(
                            candidate.total_cost_per_1k,
                            BUDGET_ROUNDING_PRECISION,
                        ),
                        reason=(
                            f"Switch from {most_used!r} "
                            f"(${current_resolved.total_cost_per_1k:.4f}/1k) "
                            f"to {candidate.model_id!r} "
                            f"(${candidate.total_cost_per_1k:.4f}/1k) "
                            f"— same context window, lower cost"
                        ),
                    ),
                )
                break  # Take first (cheapest) match per agent

        return suggestions

    def _check_denial(  # noqa: PLR0913
        self,
        *,
        agent_id: str,
        estimated_cost_usd: float,
        remaining: float,
        used_pct: float,
        alert_level: BudgetAlertLevel,
        projected_cost: float,
        projected_alert: BudgetAlertLevel,
    ) -> ApprovalDecision | None:
        """Check if the operation should be denied.

        Returns the denial decision, or ``None`` if not denied.
        """
        auto_deny_level = self._config.approval_auto_deny_alert_level

        if _ALERT_LEVEL_ORDER[projected_alert] >= _ALERT_LEVEL_ORDER[auto_deny_level]:
            logger.warning(
                CFO_OPERATION_DENIED,
                agent_id=agent_id,
                estimated_cost=estimated_cost_usd,
                alert_level=alert_level.value,
                projected_alert_level=projected_alert.value,
                reason="alert_level_exceeded",
            )
            return ApprovalDecision(
                approved=False,
                reason=(
                    f"Denied: projected alert level {projected_alert.value} "
                    f"meets or exceeds auto-deny threshold "
                    f"{auto_deny_level.value}"
                ),
                budget_remaining_usd=remaining,
                budget_used_percent=used_pct,
                alert_level=alert_level,
                conditions=(),
            )

        hard_stop_limit = round(
            self._budget_config.total_monthly
            * self._budget_config.alerts.hard_stop_at
            / 100,
            BUDGET_ROUNDING_PRECISION,
        )
        if projected_cost >= hard_stop_limit:
            logger.warning(
                CFO_OPERATION_DENIED,
                agent_id=agent_id,
                estimated_cost=estimated_cost_usd,
                projected_cost=projected_cost,
                hard_stop_limit=hard_stop_limit,
                reason="would_exceed_hard_stop",
            )
            return ApprovalDecision(
                approved=False,
                reason=(
                    f"Denied: projected cost ${projected_cost:.2f} "
                    f"would exceed hard stop ${hard_stop_limit:.2f}"
                ),
                budget_remaining_usd=remaining,
                budget_used_percent=used_pct,
                alert_level=alert_level,
                conditions=(),
            )

        return None

    def _build_approval_conditions(
        self,
        *,
        estimated_cost_usd: float,
        projected_alert: BudgetAlertLevel,
        projected_pct: float,
    ) -> tuple[str, ...]:
        """Build warning conditions for an approved operation."""
        conditions: list[str] = []
        warn_threshold = self._config.approval_warn_threshold_usd
        if estimated_cost_usd >= warn_threshold:
            conditions.append(
                f"High-cost operation: ${estimated_cost_usd:.2f} "
                f"(threshold: ${warn_threshold:.2f})"
            )

        if projected_alert in (BudgetAlertLevel.WARNING, BudgetAlertLevel.CRITICAL):
            conditions.append(
                f"Budget alert level is {projected_alert.value} "
                f"({projected_pct:.1f}% projected)"
            )
        return tuple(conditions)

    async def _compute_budget_pressure(self) -> float:
        """Compute current budget utilization percentage."""
        cfg = self._budget_config
        if cfg.total_monthly <= 0:
            return 0.0
        period_start = billing_period_start(cfg.reset_day)
        monthly_cost = await self._cost_tracker.get_total_cost(
            start=period_start,
        )
        return round(
            monthly_cost / cfg.total_monthly * 100,
            BUDGET_ROUNDING_PRECISION,
        )


# Re-export _classify_severity for backwards compatibility with tests
# that import it directly from optimizer.
__all__ = ["CostOptimizer", "_classify_severity"]
