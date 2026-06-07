# module-kind: complex_service
"""Budget enforcement service.

Composes :class:`~synthorg.budget.tracker.CostTracker` and
:class:`~synthorg.budget.config.BudgetConfig` to provide pre-flight
checks, in-flight budget checking, and task-boundary auto-downgrade
as described in the Cost Controls section of the Operations design page.
"""

import copy
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg.budget._enforcer_helpers import (
    _apply_downgrade,
    _build_checker_closure,
    _compute_thresholds,
)
from synthorg.budget.billing import billing_period_start, daily_period_start
from synthorg.budget.currency import format_cost
from synthorg.budget.degradation import (
    PreFlightResult,
    resolve_degradation,
)
from synthorg.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    ProjectBudgetExhaustedError,
    QuotaExhaustedError,
)
from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    always_allowed_result,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.budget import (
    BUDGET_BASELINE_ERROR,
    BUDGET_DAILY_LIMIT_EXCEEDED,
    BUDGET_ENFORCEMENT_CHECK,
    BUDGET_HARD_STOP_EXCEEDED,
    BUDGET_NOTIFICATION_FAILED,
    BUDGET_PREFLIGHT_ERROR,
    BUDGET_PROJECT_BASELINE_SOURCE,
    BUDGET_PROJECT_BUDGET_EXCEEDED,
    BUDGET_PROJECT_ENFORCEMENT_CHECK,
    BUDGET_RESOLVE_MODEL_ERROR,
    BUDGET_UTILIZATION_ERROR,
    BUDGET_UTILIZATION_QUERIED,
)
from synthorg.observability.events.notification import (
    NOTIFICATION_BUDGET_EXHAUSTED_SEND,
)
from synthorg.observability.events.quota import (
    QUOTA_CHECK_ALLOWED,
    QUOTA_CHECK_DENIED,
)
from synthorg.observability.events.risk_budget import (
    RISK_BUDGET_ENFORCEMENT_CHECK,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.degradation import DegradationResult
    from synthorg.budget.quota import QuotaCheckResult
    from synthorg.budget.quota_tracker import QuotaTracker
    from synthorg.budget.risk_tracker import RiskTracker
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.loop_protocol import BudgetChecker
    from synthorg.persistence.project_cost_aggregate_protocol import (
        ProjectCostAggregateRepository,
    )
    from synthorg.providers.routing.resolver import ModelResolver
    from synthorg.security.risk_scorer import RiskScorer

from synthorg.budget.risk_enforcer import BudgetEnforcerRiskMixin

logger = get_logger(__name__)
_DEFAULT_TIMEOUT_SEC: Final[float] = 5.0


def _current_trace_ids() -> tuple[str | None, str | None]:
    """Return ``(trace_id, span_id)`` from the active OTel span, or ``(None, None)``.

    The OpenTelemetry API import is local because the project keeps
    ``opentelemetry`` as an optional surface and the enforcer must
    still emit the warning even when tracing is unconfigured. Both
    ids are returned as hex strings so structured log sinks can route
    them through the existing trace-correlation pipeline.

    Returns:
        Tuple ``(str | None, str | None)``.
    """
    try:
        from opentelemetry import trace as _otel_trace  # noqa: PLC0415
    except ImportError:
        return (None, None)
    span = _otel_trace.get_current_span()
    span_ctx = span.get_span_context()
    if not span_ctx.is_valid:
        return (None, None)
    return (
        f"{span_ctx.trace_id:032x}",
        f"{span_ctx.span_id:016x}",
    )


class BudgetEnforcer(BudgetEnforcerRiskMixin):
    """Budget enforcement: pre-flight, in-flight, and auto-downgrade.

    Concurrency-safe via CostTracker's asyncio.Lock.  Pre-flight
    checks are best-effort under concurrency (TOCTOU); the in-flight
    checker is the true safety net.

    Args:
        budget_config: Limits and thresholds.
        cost_tracker: Spend queries.
        model_resolver: Auto-downgrade alias lookup.
        quota_tracker: Provider-level quota enforcement.
        degradation_configs: Per-provider degradation strategies.
        risk_tracker: Optional risk tracking service.
        risk_scorer: Optional risk scoring implementation.
        notification_dispatcher: Optional notification dispatcher.
        project_cost_repo: Optional durable project cost aggregate
            repository for lifetime budget enforcement.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        budget_config: BudgetConfig,
        cost_tracker: CostTracker,
        model_resolver: ModelResolver | None = None,
        quota_tracker: QuotaTracker | None = None,
        degradation_configs: Mapping[str, DegradationConfig] | None = None,
        risk_tracker: RiskTracker | None = None,
        risk_scorer: RiskScorer | None = None,
        notification_dispatcher: NotificationDispatcher | None = None,
        project_cost_repo: ProjectCostAggregateRepository | None = None,
    ) -> None:
        self._budget_config = budget_config
        self._cost_tracker = cost_tracker
        self._model_resolver = model_resolver
        self._quota_tracker = quota_tracker
        self._notification_dispatcher = notification_dispatcher
        self._project_cost_repo = project_cost_repo
        self._degradation_configs: MappingProxyType[str, DegradationConfig] | None = (
            MappingProxyType(copy.deepcopy(dict(degradation_configs)))
            if degradation_configs is not None
            else None
        )
        self._risk_tracker = risk_tracker
        self._risk_scorer = risk_scorer
        self._background_tasks = BackgroundTaskRegistry(owner="budget.enforcer")

        if budget_config.risk_budget.enabled and (
            risk_tracker is None or risk_scorer is None
        ):
            logger.warning(
                RISK_BUDGET_ENFORCEMENT_CHECK,
                reason="risk_budget_enabled_but_missing_deps",
                has_tracker=risk_tracker is not None,
                has_scorer=risk_scorer is not None,
            )

    async def stop(self, *, timeout_sec: float = _DEFAULT_TIMEOUT_SEC) -> None:
        """Drain pending budget-notification tasks.

        Mirrors :meth:`ApprovalTimeoutScheduler.stop` so the
        application shutdown path can await outstanding
        fire-and-forget notifications (monthly / daily exhaustion
        alerts) rather than dropping them. ``timeout_sec`` bounds
        the wait; on expiry the registry cancels stragglers and
        logs ``BACKGROUND_TASKS_DRAIN_TIMEOUT``.
        """
        await self._background_tasks.drain(timeout_sec=timeout_sec)

    @property
    def cost_tracker(self) -> CostTracker:
        """The underlying cost tracker."""
        return self._cost_tracker

    @property
    def risk_tracker(self) -> RiskTracker | None:
        """The optional risk tracker."""
        return self._risk_tracker

    @property
    def currency(self) -> str:
        """The configured ISO 4217 currency code."""
        return self._budget_config.currency

    async def get_budget_utilization_pct(self) -> float | None:
        """Return monthly budget utilization as a percentage (0--100+).

        Returns ``None`` when disabled (``total_monthly <= 0``) or
        when the cost query fails (graceful degradation).

        Returns:
            The matching ``float``, or ``None`` when no match is found.
        """
        cfg = self._budget_config
        if cfg.total_monthly <= 0:
            return None
        try:
            period_start = billing_period_start(cfg.reset_day)
            monthly_cost = await self._cost_tracker.get_total_cost(
                start=period_start,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, BUDGET_UTILIZATION_ERROR, exc, reason="falling_back_to_none"
            )
            return None
        else:
            pct = monthly_cost / cfg.total_monthly * 100
            logger.debug(
                BUDGET_UTILIZATION_QUERIED,
                monthly_cost=monthly_cost,
                total_monthly=cfg.total_monthly,
                utilization_pct=pct,
            )
            return pct

    async def check_can_execute(
        self,
        agent_id: str,
        *,
        provider_name: str | None = None,
        estimated_tokens: int = 0,
    ) -> PreFlightResult:
        """Pre-flight: verify monthly + daily + quota limits allow execution.

        Args:
            agent_id: Agent requesting execution.
            provider_name: Optional provider name for quota checks.
                When ``None``, quota check is skipped.
            estimated_tokens: Estimated tokens for the upcoming request.
                Forwarded to the quota tracker for token-based checks.

        Returns:
            Pre-flight result with effective provider info and
            degradation details when applicable.

        Raises:
            BudgetExhaustedError: Monthly hard stop exceeded.
            DailyLimitExceededError: Agent daily limit exceeded
                (subclass of ``BudgetExhaustedError``).
            QuotaExhaustedError: Provider quota exhausted and
                degradation could not resolve.
        """
        cfg = self._budget_config
        degradation_result: DegradationResult | None = None

        try:
            if cfg.total_monthly > 0:
                await self._check_monthly_hard_stop(cfg, agent_id)
            await self._check_daily_limit(cfg, agent_id)

            if provider_name is not None:
                degradation_result = await self._check_provider_quota(
                    agent_id,
                    provider_name,
                    estimated_tokens=estimated_tokens,
                )
        except BudgetExhaustedError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                BUDGET_PREFLIGHT_ERROR,
                exc,
                agent_id=agent_id,
                reason="falling_back_to_allow_execution",
            )
            return PreFlightResult()

        logger.debug(
            BUDGET_ENFORCEMENT_CHECK,
            agent_id=agent_id,
            result="pass",
        )
        if degradation_result is not None:
            return PreFlightResult(degradation=degradation_result)
        return PreFlightResult()

    async def check_project_budget(
        self,
        project_id: NotBlankStr,
        project_budget: float,
    ) -> None:
        """Check project-level budget and raise if exceeded.

        Returns immediately when ``project_budget <= 0`` (enforcement
        disabled).  Otherwise uses the durable project cost aggregate
        when available, providing accurate lifetime totals that survive
        the in-memory tracker's 168-hour retention window.  Falls back
        to in-memory tracking when no aggregate repository is configured
        or when the aggregate query fails.

        Args:
            project_id: Project identifier for cost lookup.
            project_budget: Total project budget (from Project.budget).

        Raises:
            ProjectBudgetExhaustedError: When project spend >= budget.
            MemoryError: Re-raised unconditionally.
            RecursionError: Re-raised unconditionally.
        """
        if project_budget <= 0:
            return

        project_cost = await self._get_project_cost(project_id)
        if project_cost is None:
            return

        logger.debug(
            BUDGET_PROJECT_ENFORCEMENT_CHECK,
            project_id=project_id,
            project_cost=project_cost,
            project_budget=project_budget,
        )

        if project_cost >= project_budget:
            logger.warning(
                BUDGET_PROJECT_BUDGET_EXCEEDED,
                project_id=project_id,
                project_cost=project_cost,
                project_budget=project_budget,
            )
            _fmt = format_cost
            _cur = self._budget_config.currency
            msg = (
                f"Project {project_id!r} budget exhausted: "
                f"{_fmt(project_cost, _cur)} >= "
                f"{_fmt(project_budget, _cur)}"
            )
            raise ProjectBudgetExhaustedError(
                msg,
                project_id=project_id,
                project_budget=project_budget,
                project_spent=project_cost,
            )

    async def check_quota(
        self,
        provider_name: str,
        *,
        estimated_tokens: int = 0,
    ) -> QuotaCheckResult:
        """Check provider quota via QuotaTracker.

        Returns always-allowed when no quota tracker is configured.

        Note:
            Unlike ``check_can_execute``, this method does **not**
            catch unexpected exceptions from the underlying
            ``QuotaTracker``.  ``check_can_execute`` wraps quota
            checks in a try/except that falls back to allowing
            execution on unexpected errors (graceful degradation),
            but direct callers of ``check_quota`` are responsible
            for their own error handling.

        Args:
            provider_name: Provider to check.
            estimated_tokens: Estimated tokens for the request.

        Returns:
            Quota check result.
        """
        if self._quota_tracker is None:
            logger.debug(
                QUOTA_CHECK_ALLOWED,
                provider=provider_name,
                reason="no_quota_tracker",
            )
            return always_allowed_result(provider_name)

        return await self._quota_tracker.check_quota(
            provider_name,
            estimated_tokens=estimated_tokens,
        )

    async def _check_provider_quota(
        self,
        agent_id: str,
        provider_name: str,
        *,
        estimated_tokens: int = 0,
    ) -> DegradationResult | None:
        """Check provider quota; apply degradation if exhausted.

        Returns ``None`` when quota is fine.  Returns a
        ``DegradationResult`` when a non-ALERT degradation strategy
        resolves the exhaustion.  Raises ``QuotaExhaustedError`` for
        ALERT or when degradation fails.

        Returns:
            The resulting ``DegradationResult``, or ``None`` when unavailable.

        Raises:
            QuotaExhaustedError: If the relevant budget or quota is exhausted.
            RuntimeError: If the operation fails at runtime.
            BudgetExhaustedError: If the relevant budget or quota is exhausted.
        """
        quota_result = await self.check_quota(
            provider_name,
            estimated_tokens=estimated_tokens,
        )
        if quota_result.allowed:
            return None

        logger.warning(
            QUOTA_CHECK_DENIED,
            agent_id=agent_id,
            provider=provider_name,
            reason=quota_result.reason,
        )

        deg_config = self._get_degradation_config(provider_name)
        if deg_config.strategy == DegradationAction.ALERT:
            msg = f"Provider {provider_name!r} quota exhausted: {quota_result.reason}"
            raise QuotaExhaustedError(
                msg,
                provider_name=provider_name,
                degradation_action=DegradationAction.ALERT,
            )

        if self._quota_tracker is None:
            msg = "Degradation strategy requires quota_tracker but none is configured"
            raise RuntimeError(msg)

        # Quota is confirmed denied past this point.  Unexpected
        # errors during degradation resolution must NOT fall back to
        # allow execution -- wrap as QuotaExhaustedError so the
        # BudgetExhaustedError handler in check_can_execute re-raises.
        try:
            return await resolve_degradation(
                provider_name=provider_name,
                quota_result=quota_result,
                degradation_config=deg_config,
                quota_tracker=self._quota_tracker,
                estimated_tokens=estimated_tokens,
            )
        except BudgetExhaustedError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Degradation resolution failed for provider {provider_name!r}: {safe_error_description(exc)}"  # noqa: E501
            raise QuotaExhaustedError(
                msg,
                provider_name=provider_name,
                degradation_action=deg_config.strategy,
            ) from exc

    def _get_degradation_config(
        self,
        provider_name: str,
    ) -> DegradationConfig:
        """Look up degradation config for a provider, defaulting to ALERT.

        Returns:
            Result of type ``DegradationConfig``.
        """
        if self._degradation_configs is None:
            return DegradationConfig()
        return self._degradation_configs.get(
            provider_name,
            DegradationConfig(),
        )

    async def _check_monthly_hard_stop(
        self,
        cfg: BudgetConfig,
        agent_id: str,
    ) -> None:
        """Check monthly hard stop and raise if exceeded.

        Raises:
            BudgetExhaustedError: If the relevant budget or quota is exhausted.
        """
        period_start = billing_period_start(cfg.reset_day)
        monthly_cost = await self._cost_tracker.get_total_cost(
            start=period_start,
        )
        hard_stop_limit = round(
            cfg.total_monthly * cfg.alerts.hard_stop_at / 100,
            BUDGET_ROUNDING_PRECISION,
        )

        if monthly_cost >= hard_stop_limit:
            trace_id, span_id = _current_trace_ids()
            logger.warning(
                BUDGET_HARD_STOP_EXCEEDED,
                agent_id=agent_id,
                total_cost=monthly_cost,
                monthly_budget=cfg.total_monthly,
                hard_stop_limit=hard_stop_limit,
                trace_id=trace_id,
                span_id=span_id,
            )
            _fmt = format_cost
            _cur = cfg.currency
            msg = (
                f"Monthly budget exhausted: "
                f"{_fmt(monthly_cost, _cur)} >= "
                f"{_fmt(hard_stop_limit, _cur)} "
                f"({cfg.alerts.hard_stop_at}% of "
                f"{_fmt(cfg.total_monthly, _cur)})"
            )
            _ = self._background_tasks.spawn(
                self._notify_budget_event(
                    "Monthly budget exhausted",
                    msg,
                    "critical",
                ),
                event=NOTIFICATION_BUDGET_EXHAUSTED_SEND,
                agent_id=agent_id,
                severity="critical",
                trigger="monthly_hard_stop",
            )
            raise BudgetExhaustedError(msg)

    async def _check_daily_limit(
        self,
        cfg: BudgetConfig,
        agent_id: str,
    ) -> None:
        """Check per-agent daily limit and raise if exceeded.

        Raises:
            DailyLimitExceededError: If the related operation fails.
        """
        if cfg.per_agent_daily_limit <= 0:
            return

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
                f"{format_cost(daily_cost, cfg.currency)} >= "
                f"{format_cost(cfg.per_agent_daily_limit, cfg.currency)}"
            )
            _ = self._background_tasks.spawn(
                self._notify_budget_event(
                    "Daily agent limit exceeded",
                    msg,
                    "warning",
                ),
                event=NOTIFICATION_BUDGET_EXHAUSTED_SEND,
                agent_id=agent_id,
                severity="warning",
                trigger="daily_agent_limit",
            )
            raise DailyLimitExceededError(msg)

    async def _notify_budget_event(
        self,
        title: str,
        body: str,
        severity: str,
    ) -> None:
        """Best-effort notification for a budget event.

        Dual error-handling semantics (both layers are intentional):

        * **Expected dispatcher failures**:
          :meth:`NotificationDispatcher.dispatch` may raise for
          transient transport problems (SMTP timeout, webhook 5xx,
          etc.). Those are caught here and logged at WARNING via
          :data:`BUDGET_NOTIFICATION_FAILED` so a flaky notification
          channel can't break budget enforcement.
        * **Unexpected internal bugs**: if the method itself
          raises (e.g. a :class:`NotificationSeverity` coercion
          failure or a programming error importing the notification
          models), the exception escapes this try/except and
          bubbles up to :class:`BackgroundTaskRegistry`'s
          done-callback which logs it at ERROR via
          :data:`NOTIFICATION_SEND_FAILED`. That way silent bugs
          remain visible at a higher severity than routine
          dispatch flakes.
        """
        if self._notification_dispatcher is None:
            return
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        sev = NotificationSeverity(severity)
        try:
            await self._notification_dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.BUDGET,
                    severity=sev,
                    title=title,
                    body=body,
                    source="budget.enforcer",
                ),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                BUDGET_NOTIFICATION_FAILED,
            )

    async def resolve_model(
        self,
        identity: AgentIdentity,
    ) -> AgentIdentity:
        """Apply auto-downgrade at task boundary if threshold exceeded.

        Returns identity unchanged when downgrade is disabled, not
        applicable, or lookup fails.  Returns new ``AgentIdentity``
        with downgraded ``ModelConfig`` otherwise.

        Returns:
            Result of type ``AgentIdentity``.
        """
        cfg = self._budget_config
        downgrade = cfg.auto_downgrade

        if (
            not downgrade.enabled
            or cfg.total_monthly <= 0
            or self._model_resolver is None
        ):
            return identity

        try:
            period_start = billing_period_start(cfg.reset_day)
            monthly_cost = await self._cost_tracker.get_total_cost(
                start=period_start,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                BUDGET_RESOLVE_MODEL_ERROR,
                exc,
                agent_id=str(identity.id),
                reason="cost_tracker_query_failed",
            )
            return identity

        used_pct = round(
            monthly_cost / cfg.total_monthly * 100,
            BUDGET_ROUNDING_PRECISION,
        )

        if used_pct < downgrade.threshold:
            return identity

        return _apply_downgrade(
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
        *,
        project_id: NotBlankStr | None = None,
        project_budget: float = 0.0,
    ) -> BudgetChecker | None:
        """Create a sync BudgetChecker with pre-computed baselines.

        Checks task limit, monthly total, agent daily limit, optional
        project budget, and the per-run hard ceiling (``Task.hard_ceiling``
        with fallback to ``budget.run_hard_ceiling``). Baselines are
        snapshot-in-time (TOCTOU acceptable).  Returns ``None`` when all
        limits are disabled.

        Args:
            task: The task being executed.
            agent_id: Agent identifier.
            project_id: Optional project ID for project budget checks.
            project_budget: Total project budget (0 = disabled).

        Returns:
            The resulting ``BudgetChecker``, or ``None`` when unavailable.
        """
        cfg = self._budget_config
        task_limit = task.budget_limit
        monthly_budget = cfg.total_monthly
        daily_limit = cfg.per_agent_daily_limit
        hard_ceiling = (
            task.hard_ceiling if task.hard_ceiling is not None else cfg.run_hard_ceiling
        )

        # All enforcement disabled.
        if (
            monthly_budget <= 0
            and task_limit <= 0
            and daily_limit <= 0
            and project_budget <= 0
            and hard_ceiling <= 0
        ):
            return None

        monthly_baseline, daily_baseline = await self._compute_baselines_safe(
            cfg,
            monthly_budget,
            daily_limit,
            agent_id,
        )

        project_baseline = 0.0
        if project_id is not None and project_budget > 0:
            baseline = await self._get_project_cost(
                project_id,
                error_event=BUDGET_BASELINE_ERROR,
            )
            if baseline is not None:
                project_baseline = baseline

        thresholds = _compute_thresholds(cfg, monthly_budget)

        return _build_checker_closure(
            task_limit=task_limit,
            monthly_budget=monthly_budget,
            daily_limit=daily_limit,
            monthly_baseline=monthly_baseline,
            daily_baseline=daily_baseline,
            thresholds=thresholds,
            agent_id=agent_id,
            project_budget=project_budget,
            project_baseline=project_baseline,
            project_id=project_id or None,
            hard_ceiling=hard_ceiling,
            hard_ceiling_currency=cfg.currency,
            task_id=str(task.id),
            forecast_id=task.forecast_id,
        )

    # ── Private helpers ──────────────────────────────────────────

    async def _compute_baselines_safe(
        self,
        cfg: BudgetConfig,
        monthly_budget: float,
        daily_limit: float,
        agent_id: str,
    ) -> tuple[float, float]:
        """Compute baselines, falling back to ``(0.0, 0.0)`` on error.

        Returns:
            Tuple ``(float, float)``.
        """
        try:
            return await self._compute_baselines(
                cfg,
                monthly_budget,
                daily_limit,
                agent_id,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                BUDGET_BASELINE_ERROR,
                exc,
                agent_id=agent_id,
                reason="falling_back_to_zero_baselines",
            )
            return 0.0, 0.0

    async def _compute_baselines(
        self,
        cfg: BudgetConfig,
        monthly_budget: float,
        daily_limit: float,
        agent_id: str,
    ) -> tuple[float, float]:
        """Compute monthly and daily cost baselines.

        Returns:
            Tuple ``(float, float)``.
        """
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

    async def _get_project_cost(
        self,
        project_id: NotBlankStr,
        *,
        error_event: str = BUDGET_PREFLIGHT_ERROR,
    ) -> float | None:
        """Query project cost from durable aggregate or in-memory tracker.

        Returns:
            The matching ``float``, or ``None`` when no match is found.
        """
        if self._project_cost_repo is not None:
            try:
                aggregate = await self._project_cost_repo.get(
                    project_id,
                )
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    error_event,
                    exc,
                    project_id=project_id,
                    reason="project_cost_aggregate_query_failed",
                )
            else:
                raw = aggregate.total_cost if aggregate else 0.0
                cost = round(raw, BUDGET_ROUNDING_PRECISION)
                logger.debug(
                    BUDGET_PROJECT_BASELINE_SOURCE,
                    project_id=project_id,
                    source="aggregate",
                    cost=cost,
                )
                return cost

        try:
            cost = await self._cost_tracker.get_project_cost(
                project_id,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                error_event,
                exc,
                project_id=project_id,
                reason="project_cost_query_failed",
            )
            return None
        else:
            logger.debug(
                BUDGET_PROJECT_BASELINE_SOURCE,
                project_id=project_id,
                source="in_memory",
                cost=cost,
            )
            return cost
