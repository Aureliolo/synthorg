"""Pure helper functions for budget enforcement.

Extracted from :mod:`synthorg.budget.enforcer` to keep the main
module under the 800-line limit.  All functions here are module-level
pure helpers (or closure builders) consumed only by ``BudgetEnforcer``.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple, get_args
from uuid import UUID  # noqa: TC003 -- runtime annotation

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.errors import RunHardCeilingExceededError
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.types import ModelTier
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_ALERT_THRESHOLD_CROSSED,
    BUDGET_DAILY_LIMIT_HIT,
    BUDGET_DOWNGRADE_APPLIED,
    BUDGET_DOWNGRADE_SKIPPED,
    BUDGET_HARD_CEILING_EXCEEDED,
    BUDGET_HARD_STOP_TRIGGERED,
    BUDGET_PROJECT_BUDGET_EXCEEDED,
    BUDGET_TASK_LIMIT_HIT,
    BUDGET_TIER_PRESERVED,
)

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.core.agent import AgentIdentity, ModelConfig
    from synthorg.engine.context import AgentContext
    from synthorg.engine.loop_protocol import BudgetChecker
    from synthorg.providers.routing.models import ResolvedModel
    from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)

_VALID_TIERS: frozenset[str] = frozenset(get_args(ModelTier))


# ── Downgrade helpers ────────────────────────────────────────────


def _apply_downgrade(
    identity: AgentIdentity,
    resolver: ModelResolver,
    downgrade_map: tuple[tuple[str, str], ...],
    used_pct: float,
    threshold: int,
) -> AgentIdentity:
    """Attempt model downgrade, returning identity unchanged on skip."""
    current_model_id = identity.model.model_id
    agent_id_str = str(identity.id)

    resolved = resolver.resolve_safe(current_model_id)
    if resolved is None:
        logger.debug(
            BUDGET_DOWNGRADE_SKIPPED,
            agent_id=agent_id_str,
            model_id=current_model_id,
            reason="model_not_in_resolver",
        )
        return identity

    source_alias = resolved.alias
    if source_alias is None:
        logger.debug(
            BUDGET_DOWNGRADE_SKIPPED,
            agent_id=agent_id_str,
            model_id=current_model_id,
            reason="no_alias",
        )
        return identity

    target_alias = _find_downgrade_target(source_alias, downgrade_map)
    if target_alias is None:
        logger.debug(
            BUDGET_DOWNGRADE_SKIPPED,
            agent_id=agent_id_str,
            model_id=current_model_id,
            source_alias=source_alias,
            reason="no_mapping",
        )
        return identity

    target_resolved = resolver.resolve_safe(target_alias)
    if target_resolved is None:
        logger.warning(
            BUDGET_DOWNGRADE_SKIPPED,
            agent_id=agent_id_str,
            source_alias=source_alias,
            target_alias=target_alias,
            reason="target_not_resolvable",
        )
        return identity

    new_model = _build_downgraded_model_config(
        identity.model,
        target_resolved,
        target_alias=target_alias,
    )

    logger.info(
        BUDGET_DOWNGRADE_APPLIED,
        agent_id=agent_id_str,
        from_model=current_model_id,
        from_alias=source_alias,
        to_model=target_resolved.model_id,
        to_alias=target_alias,
        used_pct=used_pct,
        threshold=threshold,
    )

    return identity.model_copy(update={"model": new_model})


def _find_downgrade_target(
    source_alias: str,
    downgrade_map: tuple[tuple[str, str], ...],
) -> str | None:
    """Find the target alias for a source in the downgrade map."""
    for src, tgt in downgrade_map:
        if src == source_alias:
            return tgt
    return None


def _build_downgraded_model_config(
    current: ModelConfig,
    target: ResolvedModel,
    *,
    target_alias: str | None = None,
) -> ModelConfig:
    """Build a new ModelConfig with the downgraded model and provider.

    Sets ``model_tier`` to *target_alias* when it is a valid tier name
    (``"large"``, ``"medium"``, ``"small"``); preserves the current
    ``model_tier`` otherwise (avoids silent tier erasure when
    downgrading to a non-tier alias like ``"local-small"``).
    """
    update: dict[str, object] = {
        "provider": target.provider_name,
        "model_id": target.model_id,
    }
    if target_alias is not None and target_alias in _VALID_TIERS:
        update["model_tier"] = target_alias
    elif current.model_tier is not None:
        logger.debug(
            BUDGET_TIER_PRESERVED,
            note="target alias is not a canonical tier name",
            current_tier=current.model_tier,
            target_alias=target_alias,
        )
    return current.model_copy(update=update)


# ── Alert helpers ────────────────────────────────────────────────


_raw_order: dict[BudgetAlertLevel, int] = {
    BudgetAlertLevel.NORMAL: 0,
    BudgetAlertLevel.WARNING: 1,
    BudgetAlertLevel.CRITICAL: 2,
    BudgetAlertLevel.HARD_STOP: 3,
}

if set(_raw_order) != set(BudgetAlertLevel):
    msg = (
        f"_ALERT_LEVEL_ORDER keys {set(_raw_order)} do not match "
        f"BudgetAlertLevel members {set(BudgetAlertLevel)}"
    )
    raise RuntimeError(msg)
if len(set(_raw_order.values())) != len(BudgetAlertLevel):
    msg = (
        f"_ALERT_LEVEL_ORDER values must be unique, got: {sorted(_raw_order.values())}"
    )
    raise RuntimeError(msg)

_ALERT_LEVEL_ORDER: MappingProxyType[BudgetAlertLevel, int] = MappingProxyType(
    _raw_order,
)
del _raw_order


def _emit_alert(
    level: BudgetAlertLevel,
    last_alert: list[BudgetAlertLevel],
    agent_id: str,
    total_cost: float,
    monthly_budget: float,
) -> None:
    """Log an alert if the level is higher than the last emitted.

    ``last_alert`` is a single-element list used as a mutable cell
    to track state across closure invocations.
    """
    if _ALERT_LEVEL_ORDER[level] <= _ALERT_LEVEL_ORDER[last_alert[0]]:
        return

    last_alert[0] = level

    if level in (BudgetAlertLevel.WARNING, BudgetAlertLevel.CRITICAL):
        logger.warning(
            BUDGET_ALERT_THRESHOLD_CROSSED,
            agent_id=agent_id,
            alert_level=level.value,
            total_cost=total_cost,
            monthly_budget=monthly_budget,
        )
    elif level == BudgetAlertLevel.HARD_STOP:
        logger.error(
            BUDGET_HARD_STOP_TRIGGERED,
            agent_id=agent_id,
            total_cost=total_cost,
            monthly_budget=monthly_budget,
        )


class _AlertThresholds(NamedTuple):
    """Pre-computed alert thresholds in ascending order."""

    warn: float
    critical: float
    hard_stop: float


def _compute_thresholds(
    cfg: BudgetConfig,
    monthly_budget: float,
) -> _AlertThresholds:
    """Pre-compute warn, critical, and hard_stop limits."""
    if monthly_budget <= 0:
        return _AlertThresholds(0.0, 0.0, 0.0)
    return _AlertThresholds(
        warn=round(
            monthly_budget * cfg.alerts.warn_at / 100,
            BUDGET_ROUNDING_PRECISION,
        ),
        critical=round(
            monthly_budget * cfg.alerts.critical_at / 100,
            BUDGET_ROUNDING_PRECISION,
        ),
        hard_stop=round(
            monthly_budget * cfg.alerts.hard_stop_at / 100,
            BUDGET_ROUNDING_PRECISION,
        ),
    )


# ── Checker closure ──────────────────────────────────────────────


def _build_checker_closure(  # noqa: PLR0913
    *,
    task_limit: float,
    monthly_budget: float,
    daily_limit: float,
    monthly_baseline: float,
    daily_baseline: float,
    thresholds: _AlertThresholds,
    agent_id: str,
    project_budget: float = 0.0,
    project_baseline: float = 0.0,
    project_id: str | None = None,
    hard_ceiling: float = 0.0,
    hard_ceiling_currency: str = "USD",
    task_id: str | None = None,
    forecast_id: UUID | None = None,
) -> BudgetChecker:
    """Build the sync budget checker closure.

    Args:
        task_limit: Per-task cost limit (0 = disabled).
        monthly_budget: Total monthly budget (0 = disabled).
        daily_limit: Per-agent daily limit (0 = disabled).
        monthly_baseline: Pre-computed monthly spend at task start.
        daily_baseline: Pre-computed daily spend at task start.
        thresholds: Pre-computed alert thresholds.
        agent_id: Agent identifier for logging.
        project_budget: Total project budget (0 = disabled).
        project_baseline: Pre-computed project spend at task start.
        project_id: Project identifier for logging (None when
            project budget is disabled).
        hard_ceiling: Per-run absolute hard ceiling (0 = disabled);
            the closure raises :class:`RunHardCeilingExceededError`
            when the running task cost meets or exceeds this value.
        hard_ceiling_currency: ISO 4217 code stamped on the raised
            error (matches the ``budget.currency`` setting).
        task_id: Task identifier carried on the ceiling error so the
            engine can route the parked context correctly.
        forecast_id: Linked forecast row identifier carried on the
            ceiling error so the dashboard can show the original
            estimate next to the accumulated cost.

    Returns:
        Sync callable returning ``True`` when budget is exhausted.

    Raises:
        RunHardCeilingExceededError: When ``hard_ceiling > 0`` and
            ``ctx.accumulated_cost.cost >= hard_ceiling``.
    """
    last_alert: list[BudgetAlertLevel] = [BudgetAlertLevel.NORMAL]

    def _check(ctx: AgentContext) -> bool:
        running_cost = ctx.accumulated_cost.cost
        if hard_ceiling > 0 and running_cost >= hard_ceiling:
            _raise_hard_ceiling(
                running_cost=running_cost,
                hard_ceiling=hard_ceiling,
                currency=hard_ceiling_currency,
                agent_id=agent_id,
                task_id=task_id,
                forecast_id=forecast_id,
            )
        return (
            _check_task_limit(running_cost, task_limit, agent_id)
            or _check_project_limit(
                running_cost,
                project_budget,
                project_baseline,
                agent_id,
                project_id,
            )
            or _check_monthly_limit(
                running_cost,
                monthly_budget,
                monthly_baseline,
                thresholds,
                last_alert,
                agent_id,
            )
            or _check_daily_limit(
                running_cost,
                daily_limit,
                daily_baseline,
                agent_id,
            )
        )

    return _check


def _raise_hard_ceiling(  # noqa: PLR0913 -- error carries every payload field
    *,
    running_cost: float,
    hard_ceiling: float,
    currency: str,
    agent_id: str,
    task_id: str | None,
    forecast_id: UUID | None,
) -> None:
    """Emit the ceiling-exceeded log + raise the typed error."""
    logger.error(
        BUDGET_HARD_CEILING_EXCEEDED,
        agent_id=agent_id,
        task_id=task_id,
        forecast_id=str(forecast_id) if forecast_id is not None else None,
        accumulated_cost=running_cost,
        hard_ceiling=hard_ceiling,
        currency=currency,
    )
    msg = (
        f"Run hard ceiling exceeded: accumulated {running_cost:.4f} "
        f"{currency} >= ceiling {hard_ceiling:.4f} {currency}"
    )
    raise RunHardCeilingExceededError(
        msg,
        ceiling_amount=hard_ceiling,
        accumulated_cost=running_cost,
        currency=currency,
        task_id=task_id,
        forecast_id=forecast_id,
    )


def _check_task_limit(
    running_cost: float,
    task_limit: float,
    agent_id: str,
) -> bool:
    """Return True if task budget limit is exhausted."""
    if task_limit > 0 and running_cost >= task_limit:
        logger.warning(
            BUDGET_TASK_LIMIT_HIT,
            agent_id=agent_id,
            running_cost=running_cost,
            task_limit=task_limit,
        )
        return True
    return False


def _check_monthly_limit(  # noqa: PLR0913
    running_cost: float,
    monthly_budget: float,
    monthly_baseline: float,
    thresholds: _AlertThresholds,
    last_alert: list[BudgetAlertLevel],
    agent_id: str,
) -> bool:
    """Return True if monthly hard stop is hit; emit alerts."""
    if monthly_budget <= 0:
        return False
    total_monthly = round(
        monthly_baseline + running_cost,
        BUDGET_ROUNDING_PRECISION,
    )
    if total_monthly >= thresholds.hard_stop:
        _emit_alert(
            BudgetAlertLevel.HARD_STOP,
            last_alert,
            agent_id,
            total_monthly,
            monthly_budget,
        )
        return True
    if total_monthly >= thresholds.critical:
        _emit_alert(
            BudgetAlertLevel.CRITICAL,
            last_alert,
            agent_id,
            total_monthly,
            monthly_budget,
        )
    elif total_monthly >= thresholds.warn:
        _emit_alert(
            BudgetAlertLevel.WARNING,
            last_alert,
            agent_id,
            total_monthly,
            monthly_budget,
        )
    return False


def _check_daily_limit(
    running_cost: float,
    daily_limit: float,
    daily_baseline: float,
    agent_id: str,
) -> bool:
    """Return True if daily limit is exhausted."""
    if daily_limit <= 0:
        return False
    total_daily = round(
        daily_baseline + running_cost,
        BUDGET_ROUNDING_PRECISION,
    )
    if total_daily >= daily_limit:
        logger.warning(
            BUDGET_DAILY_LIMIT_HIT,
            agent_id=agent_id,
            total_daily=total_daily,
            daily_limit=daily_limit,
        )
        return True
    return False


def _check_project_limit(
    running_cost: float,
    project_budget: float,
    project_baseline: float,
    agent_id: str,
    project_id: str | None = None,
) -> bool:
    """Return True if project budget is exhausted."""
    if project_budget <= 0:
        return False
    total_project = round(
        project_baseline + running_cost,
        BUDGET_ROUNDING_PRECISION,
    )
    if total_project >= project_budget:
        logger.warning(
            BUDGET_PROJECT_BUDGET_EXCEEDED,
            agent_id=agent_id,
            project_id=project_id,
            total_project=total_project,
            project_budget=project_budget,
        )
        return True
    return False
