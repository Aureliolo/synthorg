"""CFO cost optimization service.

Provides spending anomaly detection, cost efficiency analysis, model
downgrade recommendations, and operation approval decisions.  Composes
:class:`~ai_company.budget.tracker.CostTracker` and
:class:`~ai_company.budget.config.BudgetConfig` for read-only analytical
queries — the advisory complement to
:class:`~ai_company.budget.enforcer.BudgetEnforcer`.

Service layer backing the CFO role (DESIGN_SPEC Section 10.3).
"""

import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ai_company.budget.billing import billing_period_start
from ai_company.budget.enums import BudgetAlertLevel
from ai_company.budget.optimizer_models import (
    AgentEfficiency,
    AnomalyDetectionResult,
    AnomalySeverity,
    AnomalyType,
    ApprovalDecision,
    CostOptimizerConfig,
    DowngradeAnalysis,
    DowngradeRecommendation,
    EfficiencyAnalysis,
    EfficiencyRating,
    SpendingAnomaly,
)
from ai_company.constants import BUDGET_ROUNDING_PRECISION
from ai_company.observability import get_logger
from ai_company.observability.events.cfo import (
    CFO_ANOMALY_DETECTED,
    CFO_ANOMALY_SCAN_COMPLETE,
    CFO_APPROVAL_EVALUATED,
    CFO_DOWNGRADE_RECOMMENDED,
    CFO_EFFICIENCY_ANALYSIS_COMPLETE,
    CFO_OPERATION_DENIED,
    CFO_OPTIMIZER_CREATED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ai_company.budget.config import BudgetConfig
    from ai_company.budget.cost_record import CostRecord
    from ai_company.budget.tracker import CostTracker
    from ai_company.providers.routing.models import ResolvedModel
    from ai_company.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)

# ── Alert level ordering (reused from enforcer pattern) ──────────

_ALERT_LEVEL_ORDER: dict[BudgetAlertLevel, int] = {
    BudgetAlertLevel.NORMAL: 0,
    BudgetAlertLevel.WARNING: 1,
    BudgetAlertLevel.CRITICAL: 2,
    BudgetAlertLevel.HARD_STOP: 3,
}


class CostOptimizer:
    """CFO analytical service for cost optimization.

    Composes CostTracker and BudgetConfig for read-only analysis:
    anomaly detection, efficiency analysis, downgrade recommendations,
    and operation approval evaluation.

    Args:
        cost_tracker: Cost tracking service for querying spend.
        budget_config: Budget configuration for limits and thresholds.
        config: Optimizer-specific configuration. Defaults to
            ``CostOptimizerConfig()`` when ``None``.
        model_resolver: Optional model resolver for downgrade
            recommendations.
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
                into.  Must be >= 2.

        Returns:
            Anomaly detection result with any detected anomalies.

        Raises:
            ValueError: If ``start >= end`` or ``window_count < 2``.
        """
        if window_count < 2:  # noqa: PLR2004
            msg = f"window_count must be >= 2, got {window_count}"
            raise ValueError(msg)

        now = datetime.now(UTC)
        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )

        total_duration = end - start
        window_duration = total_duration / window_count
        window_starts = tuple(start + window_duration * i for i in range(window_count))

        agent_ids = sorted({r.agent_id for r in records})
        anomalies: list[SpendingAnomaly] = []

        for agent_id in agent_ids:
            window_costs = _compute_window_costs(
                records,
                agent_id,
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
        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )

        by_agent: dict[str, list[CostRecord]] = defaultdict(list)
        for r in records:
            by_agent[r.agent_id].append(r)

        global_avg = _compute_global_avg_cost_per_1k(records)
        threshold_factor = self._config.inefficiency_threshold_factor

        agent_efficiencies: list[AgentEfficiency] = []
        inefficient_count = 0

        for agent_id in sorted(by_agent):
            agent_records = by_agent[agent_id]
            total_cost = round(
                math.fsum(r.cost_usd for r in agent_records),
                BUDGET_ROUNDING_PRECISION,
            )
            total_tokens = sum(r.input_tokens + r.output_tokens for r in agent_records)
            cost_per_1k = _compute_cost_per_1k(total_cost, total_tokens)
            rating = _rate_efficiency(cost_per_1k, global_avg, threshold_factor)

            if rating == EfficiencyRating.INEFFICIENT:
                inefficient_count += 1

            agent_efficiencies.append(
                AgentEfficiency(
                    agent_id=agent_id,
                    total_cost_usd=total_cost,
                    total_tokens=total_tokens,
                    cost_per_1k_tokens=cost_per_1k,
                    record_count=len(agent_records),
                    efficiency_rating=rating,
                ),
            )

        # Sort by cost_per_1k descending (most expensive first)
        agent_efficiencies.sort(
            key=lambda a: a.cost_per_1k_tokens,
            reverse=True,
        )

        result = EfficiencyAnalysis(
            agents=tuple(agent_efficiencies),
            global_avg_cost_per_1k=global_avg,
            analysis_period_start=start,
            analysis_period_end=end,
            inefficient_agent_count=inefficient_count,
        )

        logger.info(
            CFO_EFFICIENCY_ANALYSIS_COMPLETE,
            agent_count=len(agent_efficiencies),
            inefficient_count=inefficient_count,
            global_avg_cost_per_1k=global_avg,
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
        if self._model_resolver is None:
            return DowngradeAnalysis(
                recommendations=(),
                total_estimated_monthly_savings=0.0,
                budget_pressure_percent=0.0,
            )

        efficiency = await self.analyze_efficiency(start=start, end=end)
        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )

        downgrade_map = dict(self._budget_config.auto_downgrade.downgrade_map)
        budget_pressure = await self._compute_budget_pressure()

        recommendations: list[DowngradeRecommendation] = []
        total_savings = 0.0

        for agent in efficiency.agents:
            if agent.efficiency_rating != EfficiencyRating.INEFFICIENT:
                continue

            most_used_model = _find_most_used_model(records, agent.agent_id)
            if most_used_model is None:
                continue

            recommendation = _build_downgrade_recommendation(
                agent_id=agent.agent_id,
                current_model=most_used_model,
                downgrade_map=downgrade_map,
                resolver=self._model_resolver,
            )
            if recommendation is not None:
                recommendations.append(recommendation)
                total_savings += recommendation.estimated_savings_per_1k
                logger.info(
                    CFO_DOWNGRADE_RECOMMENDED,
                    agent_id=agent.agent_id,
                    current_model=most_used_model,
                    recommended_model=recommendation.recommended_model,
                    estimated_savings=recommendation.estimated_savings_per_1k,
                )

        return DowngradeAnalysis(
            recommendations=tuple(recommendations),
            total_estimated_monthly_savings=round(
                total_savings,
                BUDGET_ROUNDING_PRECISION,
            ),
            budget_pressure_percent=budget_pressure,
        )

    async def evaluate_operation(
        self,
        *,
        agent_id: str,
        estimated_cost_usd: float,
        now: datetime | None = None,
    ) -> ApprovalDecision:
        """Evaluate whether an operation should proceed.

        Checks current budget utilization and determines if the
        estimated cost is acceptable.

        Args:
            agent_id: Agent requesting the operation.
            estimated_cost_usd: Estimated cost of the operation.
            now: Reference timestamp for billing period computation.
                Defaults to ``datetime.now(UTC)``.

        Returns:
            Approval decision with reasoning.
        """
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

        auto_deny_level = self._config.approval_auto_deny_alert_level

        # Auto-deny if at or above auto-deny alert level
        if _ALERT_LEVEL_ORDER[alert_level] >= _ALERT_LEVEL_ORDER[auto_deny_level]:
            logger.warning(
                CFO_OPERATION_DENIED,
                agent_id=agent_id,
                estimated_cost=estimated_cost_usd,
                alert_level=alert_level.value,
                reason="alert_level_exceeded",
            )
            return ApprovalDecision(
                approved=False,
                reason=(
                    f"Denied: alert level {alert_level.value} "
                    f"meets or exceeds auto-deny threshold "
                    f"{auto_deny_level.value}"
                ),
                budget_remaining_usd=remaining,
                budget_used_percent=used_pct,
                alert_level=alert_level,
                conditions=(),
            )

        # Auto-deny if estimated cost would push past hard stop
        hard_stop_limit = round(
            cfg.total_monthly * cfg.alerts.hard_stop_at / 100,
            BUDGET_ROUNDING_PRECISION,
        )
        projected_cost = round(
            monthly_cost + estimated_cost_usd,
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

        # Approve with conditions if cost is high
        conditions: list[str] = []
        warn_threshold = self._config.approval_warn_threshold_usd
        if estimated_cost_usd >= warn_threshold:
            conditions.append(
                f"High-cost operation: ${estimated_cost_usd:.2f} "
                f"(threshold: ${warn_threshold:.2f})"
            )

        if alert_level in (BudgetAlertLevel.WARNING, BudgetAlertLevel.CRITICAL):
            conditions.append(
                f"Budget alert level is {alert_level.value} ({used_pct:.1f}% used)"
            )

        logger.info(
            CFO_APPROVAL_EVALUATED,
            agent_id=agent_id,
            approved=True,
            estimated_cost=estimated_cost_usd,
            alert_level=alert_level.value,
            conditions_count=len(conditions),
        )

        return ApprovalDecision(
            approved=True,
            reason="Approved",
            budget_remaining_usd=remaining,
            budget_used_percent=used_pct,
            alert_level=alert_level,
            conditions=tuple(conditions),
        )

    # ── Private helpers ──────────────────────────────────────────

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


# ── Module-level pure helpers ────────────────────────────────────


def _compute_window_costs(
    records: Sequence[CostRecord],
    agent_id: str,
    window_starts: tuple[datetime, ...],
    window_duration: timedelta,
) -> tuple[float, ...]:
    """Compute per-window cost for a single agent."""
    costs: list[float] = []
    for ws in window_starts:
        window_end = ws + window_duration
        window_cost = math.fsum(
            r.cost_usd
            for r in records
            if r.agent_id == agent_id and r.timestamp >= ws and r.timestamp < window_end
        )
        costs.append(round(window_cost, BUDGET_ROUNDING_PRECISION))
    return tuple(costs)


def _detect_spike_anomaly(  # noqa: PLR0913
    agent_id: str,
    window_costs: tuple[float, ...],
    now: datetime,
    window_starts: tuple[datetime, ...],
    window_duration: timedelta,
    config: CostOptimizerConfig,
) -> SpendingAnomaly | None:
    """Detect a spike anomaly for a single agent.

    Returns ``None`` if no anomaly is detected or insufficient data.
    """
    if len(window_costs) < config.min_anomaly_windows:
        return None

    historical = window_costs[:-1]
    current = window_costs[-1]

    if current == 0.0:
        return None

    mean = statistics.mean(historical)

    if mean == 0.0:
        # No historical spending — a spike from zero is always flagged
        if current > 0:
            return SpendingAnomaly(
                agent_id=agent_id,
                anomaly_type=AnomalyType.SPIKE,
                severity=AnomalySeverity.HIGH,
                description=(
                    f"Agent {agent_id!r} went from $0.00 baseline "
                    f"to ${current:.2f} in the latest window"
                ),
                current_value=current,
                baseline_value=0.0,
                deviation_factor=0.0,
                detected_at=now,
                period_start=window_starts[-1],
                period_end=window_starts[-1] + window_duration,
            )
        return None

    # Check spike factor (independent of stddev)
    is_spike = current > config.anomaly_spike_factor * mean

    # Check sigma threshold
    stddev = statistics.stdev(historical) if len(historical) > 1 else 0.0
    deviation = (current - mean) / stddev if stddev > 0 else 0.0
    is_sigma_anomaly = stddev > 0 and deviation > config.anomaly_sigma_threshold

    if not is_spike and not is_sigma_anomaly:
        return None

    severity = _classify_severity(deviation)

    return SpendingAnomaly(
        agent_id=agent_id,
        anomaly_type=AnomalyType.SPIKE,
        severity=severity,
        description=(
            f"Agent {agent_id!r} spent ${current:.2f} vs "
            f"${mean:.2f} baseline ({deviation:.1f} sigma)"
        ),
        current_value=current,
        baseline_value=round(mean, BUDGET_ROUNDING_PRECISION),
        deviation_factor=round(deviation, BUDGET_ROUNDING_PRECISION),
        detected_at=now,
        period_start=window_starts[-1],
        period_end=window_starts[-1] + window_duration,
    )


def _classify_severity(deviation: float) -> AnomalySeverity:
    """Classify anomaly severity from deviation factor."""
    if deviation >= 3.0:  # noqa: PLR2004
        return AnomalySeverity.HIGH
    if deviation >= 2.0:  # noqa: PLR2004
        return AnomalySeverity.MEDIUM
    return AnomalySeverity.LOW


def _compute_cost_per_1k(total_cost: float, total_tokens: int) -> float:
    """Compute cost per 1000 tokens, returning 0 for zero tokens."""
    if total_tokens == 0:
        return 0.0
    return round(total_cost / total_tokens * 1000, BUDGET_ROUNDING_PRECISION)


def _rate_efficiency(
    cost_per_1k: float,
    global_avg: float,
    threshold_factor: float,
) -> EfficiencyRating:
    """Rate an agent's cost efficiency relative to global average."""
    if global_avg == 0.0:
        return EfficiencyRating.NORMAL
    if cost_per_1k > threshold_factor * global_avg:
        return EfficiencyRating.INEFFICIENT
    if cost_per_1k < 0.8 * global_avg:
        return EfficiencyRating.EFFICIENT
    return EfficiencyRating.NORMAL


def _compute_global_avg_cost_per_1k(
    records: Sequence[CostRecord],
) -> float:
    """Compute global average cost per 1000 tokens across all records."""
    total_cost = math.fsum(r.cost_usd for r in records)
    total_tokens = sum(r.input_tokens + r.output_tokens for r in records)
    return _compute_cost_per_1k(total_cost, total_tokens)


def _find_most_used_model(
    records: Sequence[CostRecord],
    agent_id: str,
) -> str | None:
    """Find the model most frequently used by an agent."""
    model_counts: dict[str, int] = defaultdict(int)
    for r in records:
        if r.agent_id == agent_id:
            model_counts[r.model] += 1
    if not model_counts:
        return None
    return max(model_counts, key=lambda m: model_counts[m])


def _build_downgrade_recommendation(
    *,
    agent_id: str,
    current_model: str,
    downgrade_map: dict[str, str],
    resolver: ModelResolver,
) -> DowngradeRecommendation | None:
    """Build a downgrade recommendation for a single agent."""
    current_resolved = resolver.resolve_safe(current_model)
    if current_resolved is None:
        return None

    # Check downgrade map for known path
    source_alias = current_resolved.alias
    target_ref: str | None = None

    if source_alias is not None:
        target_ref = downgrade_map.get(source_alias)

    if target_ref is None:
        # Try to find any cheaper model
        cheaper = _find_cheaper_model(current_resolved.total_cost_per_1k, resolver)
        if cheaper is None:
            return None
        target_ref = cheaper.model_id

    target_resolved = resolver.resolve_safe(target_ref)
    if target_resolved is None:
        return None

    savings = round(
        current_resolved.total_cost_per_1k - target_resolved.total_cost_per_1k,
        BUDGET_ROUNDING_PRECISION,
    )
    if savings <= 0:
        return None

    return DowngradeRecommendation(
        agent_id=agent_id,
        current_model=current_model,
        recommended_model=target_resolved.model_id,
        estimated_savings_per_1k=savings,
        reason=(
            f"Switch from {current_model!r} "
            f"(${current_resolved.total_cost_per_1k:.4f}/1k) to "
            f"{target_resolved.model_id!r} "
            f"(${target_resolved.total_cost_per_1k:.4f}/1k)"
        ),
    )


def _find_cheaper_model(
    current_cost_per_1k: float,
    resolver: ModelResolver,
) -> ResolvedModel | None:
    """Find the cheapest model that costs less than the current one."""
    all_models = resolver.all_models_sorted_by_cost()
    for model in all_models:
        if model.total_cost_per_1k < current_cost_per_1k:
            return model
    return None


def _compute_alert_level(
    used_pct: float,
    cfg: BudgetConfig,
) -> BudgetAlertLevel:
    """Compute alert level from budget usage percentage."""
    alerts = cfg.alerts
    if used_pct >= alerts.hard_stop_at:
        return BudgetAlertLevel.HARD_STOP
    if used_pct >= alerts.critical_at:
        return BudgetAlertLevel.CRITICAL
    if used_pct >= alerts.warn_at:
        return BudgetAlertLevel.WARNING
    return BudgetAlertLevel.NORMAL
