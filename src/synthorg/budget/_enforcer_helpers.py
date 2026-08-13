"""Pure helper functions for budget enforcement.

Extracted from :mod:`synthorg.budget.enforcer` to keep the main
module under the 800-line limit.  All functions here are module-level
pure helpers (or closure builders) consumed only by ``BudgetEnforcer``.
"""

from collections.abc import Callable
from types import MappingProxyType
from typing import NamedTuple, Protocol, get_args, runtime_checkable
from uuid import UUID

from synthorg.budget._run_ceilings import (
    NO_MONEY_CEILING,
    MoneyCeiling,
    raise_hard_ceiling,
    raise_hard_token_ceiling,
)
from synthorg.budget.config import BudgetConfig
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import CapabilityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_ALERT_THRESHOLD_CROSSED,
    BUDGET_DAILY_LIMIT_HIT,
    BUDGET_DOWNGRADE_APPLIED,
    BUDGET_DOWNGRADE_SKIPPED,
    BUDGET_HARD_STOP_TRIGGERED,
    BUDGET_PROJECT_BUDGET_EXCEEDED,
    BUDGET_TASK_LIMIT_HIT,
    BUDGET_TIER_PRESERVED,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)

_VALID_TIERS: frozenset[str] = frozenset(get_args(CapabilityLevel))


@runtime_checkable
class _RunningCost(Protocol):
    """Cost leaf of ``_BudgetCheckContext``.

    Split out so ``accumulated_cost`` can be typed structurally without
    importing ``providers.TokenUsage``; satisfied by ``TokenUsage``.
    """

    @property
    def cost(self) -> float:
        """Accumulated cost so far for the run."""
        ...

    @property
    def total_tokens(self) -> int:
        """Accumulated input + output tokens so far for the run.

        Read because ``cost`` measures nothing against a provider that bills
        by flat subscription: it stays zero for the life of the run, so the
        money branch below can never fire there. Tokens are counted on every
        provider, billed or not.
        """
        ...


@runtime_checkable
class _BudgetCheckContext(Protocol):
    """Structural view of the run context the budget checker reads.

    The checker reads only the ``accumulated_cost`` leaf. Annotating against
    this leaf protocol (rather than ``engine.context.AgentContext``) keeps
    ``budget`` off the ``engine`` import: ``engine`` imports ``budget``, so a
    runtime ``engine.context`` import here would close an ``engine`` ->
    ``budget`` -> ``engine`` cold cycle. ``AgentContext`` satisfies this
    structurally, so the checker signature still resolves at runtime under
    typeguard.
    """

    @property
    def accumulated_cost(self) -> _RunningCost:
        """Running token usage and cost totals."""
        ...


# ── Downgrade helpers ────────────────────────────────────────────


def _apply_downgrade(
    identity: AgentIdentity,
    resolver: ModelResolver,
    downgrade_map: tuple[tuple[str, str], ...],
    used_pct: float,
    threshold: int,
) -> AgentIdentity:
    """Attempt model downgrade, returning identity unchanged on skip.

    Returns:
        Result of type ``AgentIdentity``.
    """
    current_model_id = identity.model.model_id
    agent_id_str = str(identity.id)

    # Resolve within the agent's own provider: a budget downgrade must keep the
    # agent on the (provider, model) pair it was assigned, never let an
    # overlapping id re-derive to a different provider.
    resolved = resolver.resolve_for_pair(identity.model.provider, current_model_id)
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

    # The downgrade target is a deliberate re-selection, so it may legitimately
    # land on a different provider (e.g. a cheaper free local model). That is an
    # explicit new (provider, model) pin, not the overlapping-id ambiguity the
    # exclusive binding guards against; the eligibility-preferring selector keeps
    # an agent-ineligible provider (e.g. a gateway) out of the pick.
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

    if not target_resolved.agent_eligible:
        # When the target alias is served only by an agent-ineligible provider,
        # the eligibility-preferring selector still returns it (no eligible
        # alternative exists). Refuse the downgrade rather than move the agent
        # onto a feature-only gateway; the agent keeps its current model.
        logger.warning(
            BUDGET_DOWNGRADE_SKIPPED,
            agent_id=agent_id_str,
            source_alias=source_alias,
            target_alias=target_alias,
            reason="target_agent_ineligible",
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
    """Find the target alias for a source in the downgrade map.

    Returns:
        The matching ``str``, or ``None`` when no match is found.
    """
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

    Sets ``capability`` to *target_alias* when it names a canonical rung
    (``"expert"``, ``"capable"``, ``"basic"``); preserves the current rung
    otherwise, so downgrading to an operator alias that is not a rung does
    not silently erase what the agent was graded at.

    Returns:
        Result of type ``ModelConfig``.
    """
    update: dict[str, object] = {
        "provider": target.provider_name,
        "model_id": target.model_id,
    }
    if target_alias is not None and target_alias in _VALID_TIERS:
        update["capability"] = target_alias
    elif current.capability is not None:
        logger.debug(
            BUDGET_TIER_PRESERVED,
            note="target alias is not a canonical capability rung",
            current_capability=current.capability,
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
    """Pre-compute warn, critical, and hard_stop limits.

    Returns:
        Result of type ``_AlertThresholds``.
    """
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
    money_ceiling: MoneyCeiling = NO_MONEY_CEILING,
    hard_token_ceiling: int = 0,
    task_id: str | None = None,
    forecast_id: UUID | None = None,
) -> Callable[[_BudgetCheckContext], bool]:
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
        money_ceiling: Per-run absolute money ceiling and its currency
            (amount 0 = disabled); the closure raises
            :class:`RunHardCeilingExceededError` when the running task
            cost meets or exceeds the amount.
        hard_token_ceiling: Per-run absolute hard token ceiling (0 =
            disabled); the closure raises
            :class:`RunHardTokenCeilingExceededError` when accumulated
            tokens meet or exceed it. Checked FIRST, because against a
            flat-rate provider the money branch below can never fire.
        task_id: Task identifier carried on the ceiling error so the
            engine can route the parked context correctly.
        forecast_id: Linked forecast row identifier carried on the
            ceiling error so the dashboard can show the original
            estimate next to the accumulated cost.

    Returns:
        Sync callable returning ``True`` when budget is exhausted.

    Raises:
        RunHardCeilingExceededError: When ``money_ceiling.amount > 0`` and
            ``ctx.accumulated_cost.cost >= money_ceiling.amount``.
        RunHardTokenCeilingExceededError: When ``hard_token_ceiling > 0``
            and ``ctx.accumulated_cost.total_tokens >= hard_token_ceiling``.
    """
    last_alert: list[BudgetAlertLevel] = [BudgetAlertLevel.NORMAL]

    def _check(ctx: _BudgetCheckContext) -> bool:
        """Return True when a budget limit is reached.

        Returns:
            ``True`` when a task, project, monthly, or daily budget limit
            is reached or exceeded, ``False`` otherwise.
        """
        running_cost = ctx.accumulated_cost.cost
        # Tokens first: against a flat-rate provider the money branch below
        # can never fire, so checking it first would leave the run unbounded
        # in exactly the case this ceiling exists for.
        running_tokens = ctx.accumulated_cost.total_tokens
        if hard_token_ceiling > 0 and running_tokens >= hard_token_ceiling:
            raise_hard_token_ceiling(
                tokens_used=running_tokens,
                token_ceiling=hard_token_ceiling,
                agent_id=agent_id,
                task_id=task_id,
            )
        if money_ceiling.amount > 0 and running_cost >= money_ceiling.amount:
            raise_hard_ceiling(
                running_cost=running_cost,
                ceiling=money_ceiling,
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
                thresholds=thresholds,
                last_alert=last_alert,
                agent_id=agent_id,
            )
            or _check_daily_limit(
                running_cost,
                daily_limit,
                daily_baseline,
                agent_id,
            )
        )

    return _check


def _check_task_limit(
    running_cost: float,
    task_limit: float,
    agent_id: str,
) -> bool:
    """Return True if task budget limit is exhausted.

    Returns:
        ``True`` when the task budget limit is reached or exceeded,
        ``False`` otherwise.
    """
    if task_limit > 0 and running_cost >= task_limit:
        logger.warning(
            BUDGET_TASK_LIMIT_HIT,
            agent_id=agent_id,
            running_cost=running_cost,
            task_limit=task_limit,
        )
        return True
    return False


def _check_monthly_limit(
    running_cost: float,
    monthly_budget: float,
    monthly_baseline: float,
    *,
    thresholds: _AlertThresholds,
    last_alert: list[BudgetAlertLevel],
    agent_id: str,
) -> bool:
    """Return True if monthly hard stop is hit; emit alerts.

    Returns:
        ``True`` when the monthly hard-stop threshold is reached,
        ``False`` otherwise (warning/critical alerts may still emit).
    """
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
    """Return True if daily limit is exhausted.

    Returns:
        ``True`` when the daily limit is reached or exceeded,
        ``False`` otherwise.
    """
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
    """Return True if project budget is exhausted.

    Returns:
        ``True`` when the project budget is reached or exceeded,
        ``False`` otherwise.
    """
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
