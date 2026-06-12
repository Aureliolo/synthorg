"""Risk budget enforcement mixin for :class:`BudgetEnforcer`."""

from datetime import UTC, datetime
from functools import partial

from synthorg.budget.billing import daily_period_start
from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import RiskBudgetExhaustedError
from synthorg.budget.risk_check import RiskCheckResult
from synthorg.budget.risk_record import RiskRecord
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.risk_budget import (
    RISK_BUDGET_DAILY_LIMIT_EXCEEDED,
    RISK_BUDGET_ENFORCEMENT_CHECK,
    RISK_BUDGET_LIMIT_EXCEEDED,
    RISK_BUDGET_RECORD_ADDED,
    RISK_BUDGET_RECORD_FAILED,
    RISK_BUDGET_TASK_LIMIT_EXCEEDED,
)
from synthorg.security.risk_scorer import RiskScorer

logger = get_logger(__name__)


class BudgetEnforcerRiskMixin:
    """Mixin providing risk-budget checks and recording."""

    _budget_config: BudgetConfig
    _risk_tracker: RiskTracker | None
    _risk_scorer: RiskScorer | None

    async def check_risk_budget(
        self,
        agent_id: str,
        task_id: str,
        action_type: str,
    ) -> RiskCheckResult:
        """Pre-flight risk budget check.

        Checks per-task, per-agent daily, and total daily risk limits
        including the projected risk of the pending action.

        Pre-flight checks are best-effort under concurrency (TOCTOU).
        See class docstring.

        Returns:
            Result of type ``RiskCheckResult``.

        Raises:
            RiskBudgetExhaustedError: When a risk limit is exceeded
                and enforcement is active.
        """
        risk_cfg = self._budget_config.risk_budget
        if not risk_cfg.enabled or self._risk_tracker is None:
            return RiskCheckResult()

        logger.debug(
            RISK_BUDGET_ENFORCEMENT_CHECK,
            agent_id=agent_id,
            task_id=task_id,
            action_type=action_type,
        )

        projected = 0.0
        try:
            if self._risk_scorer is not None:
                projected = self._risk_scorer.score(action_type).risk_units

            day_start = daily_period_start()
            t = self._risk_tracker
            checks = (
                (
                    risk_cfg.per_task_risk_limit,
                    partial(t.get_task_risk, task_id),
                    RISK_BUDGET_TASK_LIMIT_EXCEEDED,
                    "Per-task",
                ),
                (
                    risk_cfg.per_agent_daily_risk_limit,
                    partial(t.get_agent_risk, agent_id, start=day_start),
                    RISK_BUDGET_DAILY_LIMIT_EXCEEDED,
                    "Per-agent daily",
                ),
                (
                    risk_cfg.total_daily_risk_limit,
                    partial(t.get_total_risk, start=day_start),
                    RISK_BUDGET_LIMIT_EXCEEDED,
                    "Total daily",
                ),
            )
            for limit, get_risk, event, label in checks:
                self._enforce_risk_limit(
                    limit,
                    await get_risk(),
                    projected,
                    event,
                    label,
                    agent_id,
                    task_id,
                )
        except RiskBudgetExhaustedError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                RISK_BUDGET_ENFORCEMENT_CHECK,
                exc,
                agent_id=agent_id,
                task_id=task_id,
                action_type=action_type,
                reason="risk_check_error",
            )

        return RiskCheckResult(risk_units=projected)

    def _enforce_risk_limit(  # noqa: PLR0913
        self,
        limit: float,
        current: float,
        projected: float,
        event: str,
        label: str,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Check a single risk limit and raise if exceeded.

        Raises:
            RiskBudgetExhaustedError: If the relevant budget or quota is exhausted.
        """
        if limit <= 0:
            return
        total = current + projected
        if total >= limit:
            logger.warning(
                event,
                agent_id=agent_id,
                task_id=task_id,
                current=current,
                projected=projected,
                limit=limit,
            )
            msg = f"{label} risk limit exceeded: {total:.2f} >= {limit:.2f}"
            raise RiskBudgetExhaustedError(
                msg,
                agent_id=agent_id,
                task_id=task_id,
                risk_units_used=total,
                risk_limit=limit,
            )

    async def record_risk(
        self,
        agent_id: str,
        task_id: str,
        action_type: str,
    ) -> RiskRecord | None:
        """Score and record a risk entry for the given action.

        Returns:
            The resulting ``RiskRecord``, or ``None`` when unavailable.
        """
        risk_cfg = self._budget_config.risk_budget
        if (
            not risk_cfg.enabled
            or self._risk_tracker is None
            or self._risk_scorer is None
        ):
            return None

        try:
            score = self._risk_scorer.score(action_type)
            record = RiskRecord(
                agent_id=agent_id,
                task_id=task_id,
                action_type=action_type,
                risk_score=score,
                risk_units=score.risk_units,
                timestamp=datetime.now(UTC),
            )
            await self._risk_tracker.record(record)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                RISK_BUDGET_RECORD_FAILED,
                exc,
                agent_id=agent_id,
                task_id=task_id,
                action_type=action_type,
            )
            return None
        logger.info(
            RISK_BUDGET_RECORD_ADDED,
            agent_id=agent_id,
            task_id=task_id,
            action_type=action_type,
            risk_units=score.risk_units,
        )
        return record
