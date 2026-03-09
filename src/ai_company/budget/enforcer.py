"""Budget enforcement service.

Composes :class:`~ai_company.budget.tracker.CostTracker` and
:class:`~ai_company.budget.config.BudgetConfig` to provide pre-flight
checks, in-flight budget checking, and task-boundary auto-downgrade
as described in DESIGN_SPEC Section 10.4.
"""

from typing import TYPE_CHECKING

from ai_company.budget.billing import billing_period_start, daily_period_start
from ai_company.budget.enums import BudgetAlertLevel
from ai_company.constants import BUDGET_ROUNDING_PRECISION
from ai_company.engine.errors import BudgetExhaustedError, DailyLimitExceededError
from ai_company.observability import get_logger
from ai_company.observability.events.budget import (
    BUDGET_ALERT_THRESHOLD_CROSSED,
    BUDGET_DAILY_LIMIT_EXCEEDED,
    BUDGET_DOWNGRADE_APPLIED,
    BUDGET_DOWNGRADE_SKIPPED,
    BUDGET_ENFORCEMENT_CHECK,
    BUDGET_HARD_STOP_TRIGGERED,
)

if TYPE_CHECKING:
    from ai_company.budget.config import BudgetConfig
    from ai_company.budget.tracker import CostTracker
    from ai_company.core.agent import AgentIdentity, ModelConfig
    from ai_company.core.task import Task
    from ai_company.engine.context import AgentContext
    from ai_company.engine.loop_protocol import BudgetChecker
    from ai_company.providers.routing.models import ResolvedModel
    from ai_company.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)


class BudgetEnforcer:
    """Budget enforcement service composing CostTracker + BudgetConfig.

    Provides pre-flight checks (can this agent start?), in-flight budget
    checking (monthly + daily + task limits with alert emission), and
    task-boundary auto-downgrade.  Thread-safe via CostTracker's
    asyncio.Lock.

    Note: Pre-flight checks are best-effort under concurrency (TOCTOU).
    The in-flight checker is the true safety net.

    Args:
        budget_config: Budget configuration for limits and thresholds.
        cost_tracker: Cost tracking service for querying spend.
        model_resolver: Optional model resolver for auto-downgrade
            alias lookup.
    """

    def __init__(
        self,
        *,
        budget_config: BudgetConfig,
        cost_tracker: CostTracker,
        model_resolver: ModelResolver | None = None,
    ) -> None:
        self._budget_config = budget_config
        self._cost_tracker = cost_tracker
        self._model_resolver = model_resolver

    @property
    def cost_tracker(self) -> CostTracker:
        """The underlying cost tracker."""
        return self._cost_tracker

    async def check_can_execute(self, agent_id: str) -> None:
        """Pre-flight: verify monthly + daily limits allow execution.

        Raises:
            BudgetExhaustedError: Monthly hard stop exceeded.
            DailyLimitExceededError: Agent daily limit exceeded.
        """
        cfg = self._budget_config

        # Skip if enforcement disabled (total_monthly == 0)
        if cfg.total_monthly <= 0:
            logger.debug(
                BUDGET_ENFORCEMENT_CHECK,
                agent_id=agent_id,
                result="pass",
                reason="enforcement_disabled",
            )
            return

        # Monthly hard stop check
        period_start = billing_period_start(cfg.reset_day)
        monthly_cost = await self._cost_tracker.get_total_cost(
            start=period_start,
        )
        hard_stop_limit = round(
            cfg.total_monthly * cfg.alerts.hard_stop_at / 100,
            BUDGET_ROUNDING_PRECISION,
        )

        if monthly_cost >= hard_stop_limit:
            logger.warning(
                BUDGET_HARD_STOP_TRIGGERED,
                agent_id=agent_id,
                monthly_cost=monthly_cost,
                hard_stop_limit=hard_stop_limit,
            )
            msg = (
                f"Monthly budget exhausted: ${monthly_cost:.2f} >= "
                f"${hard_stop_limit:.2f} "
                f"({cfg.alerts.hard_stop_at}% of ${cfg.total_monthly:.2f})"
            )
            raise BudgetExhaustedError(msg)

        # Daily limit check
        if cfg.per_agent_daily_limit > 0:
            day_start = daily_period_start()
            daily_cost = await self._cost_tracker.get_agent_cost(
                agent_id,
                start=day_start,
            )
            if daily_cost >= cfg.per_agent_daily_limit:
                logger.warning(
                    BUDGET_DAILY_LIMIT_EXCEEDED,
                    agent_id=agent_id,
                    daily_cost=daily_cost,
                    daily_limit=cfg.per_agent_daily_limit,
                )
                msg = (
                    f"Agent {agent_id!r} daily limit exceeded: "
                    f"${daily_cost:.2f} >= "
                    f"${cfg.per_agent_daily_limit:.2f}"
                )
                raise DailyLimitExceededError(msg)

        logger.debug(
            BUDGET_ENFORCEMENT_CHECK,
            agent_id=agent_id,
            result="pass",
            monthly_cost=monthly_cost,
            hard_stop_limit=hard_stop_limit,
        )

    async def resolve_model(
        self,
        identity: AgentIdentity,
    ) -> AgentIdentity:
        """Apply auto-downgrade at task boundary if threshold exceeded.

        Returns identity unchanged when:
        - ``auto_downgrade.enabled`` is ``False``
        - budget usage below threshold
        - no ``model_resolver`` provided
        - ``model_id`` not found in resolver
        - model alias not in ``downgrade_map``
        - target alias not resolvable

        Returns new ``AgentIdentity`` with downgraded ``ModelConfig``
        otherwise.
        """
        cfg = self._budget_config
        downgrade = cfg.auto_downgrade

        if (
            not downgrade.enabled
            or cfg.total_monthly <= 0
            or self._model_resolver is None
        ):
            return identity

        # Check budget usage against downgrade threshold
        period_start = billing_period_start(cfg.reset_day)
        monthly_cost = await self._cost_tracker.get_total_cost(
            start=period_start,
        )
        used_pct = round(
            monthly_cost / cfg.total_monthly * 100,
            BUDGET_ROUNDING_PRECISION,
        )

        if used_pct < downgrade.threshold:
            return identity

        return self._apply_downgrade(
            identity,
            self._model_resolver,
            downgrade.downgrade_map,
            used_pct,
            downgrade.threshold,
        )

    async def make_budget_checker(
        self,
        task: Task,
        agent_id: str,
    ) -> BudgetChecker | None:
        """Create a sync BudgetChecker with pre-computed baselines.

        Queries CostTracker once for monthly and daily baselines, then
        returns a sync closure that checks:

        1. Task budget limit (``ctx.accumulated_cost >= task.budget_limit``)
        2. Monthly total (baseline + ctx cost >= total_monthly * hard_stop_at/100)
        3. Agent daily (baseline + ctx cost >= per_agent_daily_limit)

        Alert deduplication: the closure tracks the last emitted alert
        level and only logs upward transitions
        (NORMAL -> WARNING -> CRITICAL -> HARD_STOP).

        Returns ``None`` if all limits are disabled (total_monthly == 0,
        task has no budget limit, and daily limit == 0).
        """
        cfg = self._budget_config
        task_limit = task.budget_limit
        monthly_budget = cfg.total_monthly
        daily_limit = cfg.per_agent_daily_limit

        # All enforcement disabled
        if monthly_budget <= 0 and task_limit <= 0 and daily_limit <= 0:
            return None

        baselines = await self._compute_baselines(
            cfg,
            monthly_budget,
            daily_limit,
            agent_id,
        )

        thresholds = _compute_thresholds(cfg, monthly_budget)

        return _build_checker_closure(
            task_limit=task_limit,
            monthly_budget=monthly_budget,
            daily_limit=daily_limit,
            monthly_baseline=baselines[0],
            daily_baseline=baselines[1],
            thresholds=thresholds,
            agent_id=agent_id,
        )

    # ── Private helpers ──────────────────────────────────────────

    async def _compute_baselines(
        self,
        cfg: BudgetConfig,
        monthly_budget: float,
        daily_limit: float,
        agent_id: str,
    ) -> tuple[float, float]:
        """Compute monthly and daily cost baselines."""
        monthly_baseline = 0.0
        daily_baseline = 0.0

        if monthly_budget > 0:
            period_start = billing_period_start(cfg.reset_day)
            monthly_baseline = await self._cost_tracker.get_total_cost(
                start=period_start,
            )

        if daily_limit > 0:
            day_start = daily_period_start()
            daily_baseline = await self._cost_tracker.get_agent_cost(
                agent_id,
                start=day_start,
            )

        return monthly_baseline, daily_baseline

    @staticmethod
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
        source_alias = resolved.alias if resolved else None

        if source_alias is None:
            reason = "model_not_in_resolver" if resolved is None else "no_alias"
            logger.debug(
                BUDGET_DOWNGRADE_SKIPPED,
                agent_id=agent_id_str,
                model_id=current_model_id,
                reason=reason,
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


# ── Module-level pure helpers ────────────────────────────────────


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
) -> ModelConfig:
    """Build a new ModelConfig with the downgraded model and provider."""
    return current.model_copy(
        update={
            "provider": target.provider_name,
            "model_id": target.model_id,
        },
    )


_ALERT_LEVEL_ORDER: dict[BudgetAlertLevel, int] = {
    BudgetAlertLevel.NORMAL: 0,
    BudgetAlertLevel.WARNING: 1,
    BudgetAlertLevel.CRITICAL: 2,
    BudgetAlertLevel.HARD_STOP: 3,
}


def _emit_alert(
    level: BudgetAlertLevel,
    last_alert: list[BudgetAlertLevel],
    agent_id: str,
    total_cost: float,
    monthly_budget: float,
) -> None:
    """Log an alert if the level is higher than the last emitted."""
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


def _compute_thresholds(
    cfg: BudgetConfig,
    monthly_budget: float,
) -> tuple[float, float, float]:
    """Pre-compute hard_stop, warn, and critical limits."""
    if monthly_budget <= 0:
        return 0.0, 0.0, 0.0
    return (
        round(
            monthly_budget * cfg.alerts.hard_stop_at / 100,
            BUDGET_ROUNDING_PRECISION,
        ),
        round(
            monthly_budget * cfg.alerts.warn_at / 100,
            BUDGET_ROUNDING_PRECISION,
        ),
        round(
            monthly_budget * cfg.alerts.critical_at / 100,
            BUDGET_ROUNDING_PRECISION,
        ),
    )


def _build_checker_closure(  # noqa: PLR0913
    *,
    task_limit: float,
    monthly_budget: float,
    daily_limit: float,
    monthly_baseline: float,
    daily_baseline: float,
    thresholds: tuple[float, float, float],
    agent_id: str,
) -> BudgetChecker:
    """Build the sync budget checker closure."""
    hard_stop_limit, warn_limit, critical_limit = thresholds
    last_alert: list[BudgetAlertLevel] = [BudgetAlertLevel.NORMAL]

    def _check(ctx: AgentContext) -> bool:
        running_cost = ctx.accumulated_cost.cost_usd

        # 1. Task budget limit
        if task_limit > 0 and running_cost >= task_limit:
            return True

        # 2. Monthly hard stop + alerts
        if monthly_budget > 0:
            total_monthly = round(
                monthly_baseline + running_cost,
                BUDGET_ROUNDING_PRECISION,
            )
            if total_monthly >= hard_stop_limit:
                _emit_alert(
                    BudgetAlertLevel.HARD_STOP,
                    last_alert,
                    agent_id,
                    total_monthly,
                    monthly_budget,
                )
                return True
            if total_monthly >= critical_limit:
                _emit_alert(
                    BudgetAlertLevel.CRITICAL,
                    last_alert,
                    agent_id,
                    total_monthly,
                    monthly_budget,
                )
            elif total_monthly >= warn_limit:
                _emit_alert(
                    BudgetAlertLevel.WARNING,
                    last_alert,
                    agent_id,
                    total_monthly,
                    monthly_budget,
                )

        # 3. Agent daily limit
        if daily_limit > 0:
            total_daily = round(
                daily_baseline + running_cost,
                BUDGET_ROUNDING_PRECISION,
            )
            if total_daily >= daily_limit:
                return True

        return False

    return _check
