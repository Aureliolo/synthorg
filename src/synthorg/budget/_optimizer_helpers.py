"""Pure helper functions for the CostOptimizer service.

Extracted from ``optimizer.py`` to keep both modules under the 800-line
project limit.  All functions are module-private (prefixed with ``_``)
and stateless.
"""

import math
import statistics
from collections import defaultdict
from typing import TYPE_CHECKING

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.optimizer_models import (
    AgentEfficiency,
    AnomalySeverity,
    AnomalyType,
    CostOptimizerConfig,
    DowngradeRecommendation,
    EfficiencyAnalysis,
    EfficiencyRating,
    SpendingAnomaly,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger
from synthorg.observability.events.cfo import (
    CFO_DOWNGRADE_SKIPPED,
    CFO_INSUFFICIENT_WINDOWS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.cost_record import CostRecord
    from synthorg.providers.routing.models import ResolvedModel
    from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)

# Agents spending below this fraction of global average are rated EFFICIENT
_EFFICIENCY_LOWER_BOUND = 0.8


def _build_efficiency_from_records(
    records: Sequence[CostRecord],
    *,
    start: datetime,
    end: datetime,
    threshold_factor: float,
) -> EfficiencyAnalysis:
    """Build an EfficiencyAnalysis from pre-fetched records."""
    by_agent: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_agent[r.agent_id].append(r)

    global_avg = _compute_global_avg_cost_per_1k(records)

    agent_efficiencies: list[AgentEfficiency] = []
    for agent_id in sorted(by_agent):
        agent_records = by_agent[agent_id]
        total_cost = round(
            math.fsum(r.cost_usd for r in agent_records),
            BUDGET_ROUNDING_PRECISION,
        )
        total_tokens = sum(r.input_tokens + r.output_tokens for r in agent_records)
        cost_per_1k = _compute_cost_per_1k(total_cost, total_tokens)
        rating = _rate_efficiency(cost_per_1k, global_avg, threshold_factor)

        agent_efficiencies.append(
            AgentEfficiency(
                agent_id=agent_id,
                total_cost_usd=total_cost,
                total_tokens=total_tokens,
                record_count=len(agent_records),
                efficiency_rating=rating,
            ),
        )

    agent_efficiencies.sort(
        key=lambda a: a.cost_per_1k_tokens,
        reverse=True,
    )

    return EfficiencyAnalysis(
        agents=tuple(agent_efficiencies),
        global_avg_cost_per_1k=global_avg,
        analysis_period_start=start,
        analysis_period_end=end,
    )


def _compute_window_costs(
    agent_records: Sequence[CostRecord],
    window_starts: tuple[datetime, ...],
    window_duration: timedelta,
) -> tuple[float, ...]:
    """Compute per-window cost for a single agent's pre-filtered records."""
    costs: list[float] = []
    for ws in window_starts:
        window_end = ws + window_duration
        window_cost = math.fsum(
            r.cost_usd for r in agent_records if ws <= r.timestamp < window_end
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
        logger.debug(
            CFO_INSUFFICIENT_WINDOWS,
            agent_id=agent_id,
            window_count=len(window_costs),
            min_required=config.min_anomaly_windows,
        )
        return None

    historical = window_costs[:-1]
    current = window_costs[-1]

    if current == 0.0:
        return None

    mean = statistics.mean(historical)

    if mean == 0.0:
        # No historical spending -- spike from zero (current > 0 per guard)
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

    # Check spike factor (independent of stddev)
    spike_ratio = current / mean
    is_spike = spike_ratio > config.anomaly_spike_factor

    # Check sigma threshold
    stddev = statistics.stdev(historical) if len(historical) > 1 else 0.0
    deviation = (current - mean) / stddev if stddev > 0 else 0.0
    is_sigma_anomaly = deviation > config.anomaly_sigma_threshold

    if not is_spike and not is_sigma_anomaly:
        return None

    # When stddev is zero, use the spike ratio for severity classification
    classification_value = spike_ratio if is_spike and stddev == 0.0 else deviation
    severity = _classify_severity(classification_value)

    # Use spike_ratio as deviation_factor when stddev is zero
    effective_deviation = spike_ratio if stddev == 0.0 else deviation

    return SpendingAnomaly(
        agent_id=agent_id,
        anomaly_type=AnomalyType.SPIKE,
        severity=severity,
        description=(
            f"Agent {agent_id!r} spent ${current:.2f} vs "
            f"${mean:.2f} baseline ({effective_deviation:.1f}x)"
        ),
        current_value=current,
        baseline_value=round(mean, BUDGET_ROUNDING_PRECISION),
        deviation_factor=round(effective_deviation, BUDGET_ROUNDING_PRECISION),
        detected_at=now,
        period_start=window_starts[-1],
        period_end=window_starts[-1] + window_duration,
    )


def _classify_severity(value: float) -> AnomalySeverity:
    """Classify anomaly severity from a deviation factor or spike ratio.

    Args:
        value: A deviation factor (sigma) or spike ratio used to
            determine severity.  Thresholds: >= 3.0 -> HIGH,
            >= 2.0 -> MEDIUM, else LOW.
    """
    if value >= 3.0:  # noqa: PLR2004
        return AnomalySeverity.HIGH
    if value >= 2.0:  # noqa: PLR2004
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
    if cost_per_1k < _EFFICIENCY_LOWER_BOUND * global_avg:
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
    agent_records: Sequence[CostRecord],
) -> str | None:
    """Find the most frequently used model from pre-filtered records."""
    model_counts: dict[str, int] = defaultdict(int)
    for r in agent_records:
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
        logger.debug(
            CFO_DOWNGRADE_SKIPPED,
            agent_id=agent_id,
            reason="current_model_not_resolved",
            model=current_model,
        )
        return None

    # Check downgrade map for known path (alias-based lookup)
    source_alias = current_resolved.alias
    target_ref: str | None = None

    if source_alias is not None:
        target_ref = downgrade_map.get(source_alias)
    else:
        logger.debug(
            CFO_DOWNGRADE_SKIPPED,
            agent_id=agent_id,
            reason="no_alias_for_downgrade_map",
            model=current_model,
        )

    if target_ref is None:
        cheaper = _find_cheaper_model(
            current_resolved.total_cost_per_1k,
            resolver,
            min_context=current_resolved.max_context,
        )
        if cheaper is None:
            logger.debug(
                CFO_DOWNGRADE_SKIPPED,
                agent_id=agent_id,
                reason="no_cheaper_model_available",
                model=current_model,
            )
            return None
        target_ref = cheaper.model_id

    target_resolved = resolver.resolve_safe(target_ref)
    if target_resolved is None:
        logger.debug(
            CFO_DOWNGRADE_SKIPPED,
            agent_id=agent_id,
            reason="target_model_not_resolved",
            target=target_ref,
        )
        return None

    savings = round(
        current_resolved.total_cost_per_1k - target_resolved.total_cost_per_1k,
        BUDGET_ROUNDING_PRECISION,
    )
    if savings <= 0:
        logger.debug(
            CFO_DOWNGRADE_SKIPPED,
            agent_id=agent_id,
            reason="no_savings",
            current_cost=current_resolved.total_cost_per_1k,
            target_cost=target_resolved.total_cost_per_1k,
        )
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
    *,
    min_context: int = 0,
) -> ResolvedModel | None:
    """Find the cheapest model below current cost with sufficient context."""
    all_models = resolver.all_models_sorted_by_cost()
    for model in all_models:
        if (
            model.total_cost_per_1k < current_cost_per_1k
            and model.max_context >= min_context
        ):
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


def _group_records_by_agent(
    records: Sequence[CostRecord],
) -> dict[str, list[CostRecord]]:
    """Group records by agent_id for efficient per-agent iteration."""
    by_agent: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_agent[r.agent_id].append(r)
    return by_agent
