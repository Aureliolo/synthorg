# module-kind: service
"""Budget-halt handling for :class:`AgentEngine`.

A ceiling crossing is a controlled stop, not a crash: the run parks so an
operator can raise the ceiling and resume, and every step of that park is
best-effort so a persistence problem degrades to a plain budget stop rather
than escalating into an engine failure.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.budget.errors import (
    BudgetExhaustedError,
    RunHardCeilingExceededError,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import resolve_tracker_currency
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.prompt import SystemPrompt, build_error_prompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import BUDGET_HARD_CEILING_HALT_STAMPED
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_BUDGET_STOPPED,
    EXECUTION_ENGINE_ERROR,
)

if TYPE_CHECKING:
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

logger = get_logger(__name__)


class AgentEngineBudgetHaltMixin:
    """Mixin turning a budget exhaustion into a parked or stopped run."""

    _cost_forecast_repo: CostForecastRepository | None

    async def _handle_budget_error(
        self,
        *,
        exc: BudgetExhaustedError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
    ) -> AgentRunResult:
        """Build a BUDGET_EXHAUSTED (or PARKED, for hard ceiling) result.

        Hard-ceiling crossings route to a parked termination when the
        approval gate is wired so the operator can raise the ceiling
        and resume; the engine awaits ``ApprovalGate.park_context()``
        to persist the parked state. A persistence failure degrades
        gracefully to BUDGET_EXHAUSTED (the existing controlled-stop
        path) so a missing parked_context_repo never escalates to
        engine crash. All other ``BudgetExhaustedError`` subclasses
        (monthly / daily / project / quota) keep the original
        controlled-stop path.

        Returns:
            An :class:`AgentRunResult` whose
            ``execution_result.termination_reason`` is ``PARKED`` for
            a parked hard-ceiling crossing or ``BUDGET_EXHAUSTED`` for
            every other controlled-stop path.
        """
        logger.warning(
            EXECUTION_ENGINE_BUDGET_STOPPED,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        is_ceiling = isinstance(exc, RunHardCeilingExceededError)
        has_gate = getattr(self, "_approval_gate", None) is not None
        parked_ok = False
        if isinstance(exc, RunHardCeilingExceededError) and has_gate:
            parked_ok = await self._park_hard_ceiling(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                ctx=ctx,
            )
        try:
            error_ctx = ctx or AgentContext.from_identity(identity, task=task)
            termination = (
                TerminationReason.PARKED
                if is_ceiling and has_gate and parked_ok
                else TerminationReason.BUDGET_EXHAUSTED
            )
            budget_result = ExecutionResult(
                context=error_ctx,
                termination_reason=termination,
            )
            error_prompt = build_error_prompt(
                identity,
                agent_id,
                system_prompt,
            )
            return AgentRunResult(
                execution_result=budget_result,
                system_prompt=error_prompt,
                duration_seconds=duration_seconds,
                agent_id=agent_id,
                task_id=task_id,
                currency=resolve_tracker_currency(
                    getattr(self, "_cost_tracker", None),
                ),
            )
        except Exception as build_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(build_exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(build_exc).__name__,
                error=safe_error_description(build_exc),
                stage="build_budget_exhausted_result",
            )
            exc.add_note(
                f"Secondary failure while building budget-exhausted "
                f"result: {type(build_exc).__name__}: "
                f"{safe_error_description(build_exc)}",
            )
            raise exc from None

    async def _park_hard_ceiling(
        self,
        *,
        exc: RunHardCeilingExceededError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        ctx: AgentContext | None,
    ) -> bool:
        """Persist a parked context for a hard-ceiling crossing.

        On failure (no parked-context repo, serialization error,
        persistence error) returns ``False`` so the caller degrades
        to the BUDGET_EXHAUSTED controlled-stop path. The failure is
        logged but never re-raised: a ceiling halt must not crash the
        engine even if the persistence layer is in a bad state.

        Returns:
            ``True`` when :py:meth:`ApprovalGate.park_context` succeeds
            and the halt context was stamped on the forecast, ``False``
            on any failure (no gate, persistence error, missing repo).
        """
        from synthorg.approval.enums import ApprovalRiskLevel  # noqa: PLC0415
        from synthorg.approval.models import (  # noqa: PLC0415
            EscalationInfo,
        )

        gate: ApprovalGate | None = getattr(self, "_approval_gate", None)
        if gate is None:
            return False
        try:
            forecast_id_str = (
                str(exc.forecast_id) if exc.forecast_id is not None else "no-forecast"
            )
            reason = (
                f"Run hard ceiling exceeded: accumulated"
                f" {exc.accumulated_cost:.4f} {exc.currency}"
                f" >= ceiling {exc.ceiling_amount:.4f} {exc.currency}"
            )
            # A fresh suffix per crossing keeps the approval_id unique
            # across retries: a resumed run that re-crosses the ceiling
            # must not collide with the prior parked-context row (the
            # ParkedContext/get_by_approval lookup expects 1:1).
            escalation = EscalationInfo(
                approval_id=(
                    f"hard-ceiling-{task_id}-{forecast_id_str}-{uuid4().hex[:12]}"
                ),
                tool_call_id=f"budget-checker-{task_id}",
                tool_name="budget_checker",
                action_type="budget:hard_ceiling_exceeded",
                risk_level=ApprovalRiskLevel.HIGH,
                reason=reason,
            )
            park_ctx = ctx or AgentContext.from_identity(identity, task=task)
            await gate.park_context(
                escalation=escalation,
                context=park_ctx,
                agent_id=agent_id,
                task_id=task_id,
                # The AG-UI session is the task, so a budget-ceiling park
                # surfaces an APPROVAL_INTERRUPT on the dashboard's stream.
                session_id=task_id,
            )
        except Exception as park_exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(park_exc)
            logger.warning(
                EXECUTION_ENGINE_BUDGET_STOPPED,
                agent_id=agent_id,
                task_id=task_id,
                note="park_context failed; falling back to BUDGET_EXHAUSTED",
                error_type=type(park_exc).__name__,
                error=safe_error_description(park_exc),
            )
            return False
        await self._stamp_forecast_halt(exc, task_id=task_id)
        return True

    async def _stamp_forecast_halt(
        self,
        exc: RunHardCeilingExceededError,
        *,
        task_id: str,
    ) -> None:
        """Record halt context on the forecast row so the UI can resume.

        Best-effort: a missing repo, a missing forecast id, or any repo
        error degrades silently. The park itself already succeeded; a
        failure to stamp the read-side halt marker must never turn a
        clean ceiling halt into a crash.
        """
        from synthorg.budget.forecast_models import HaltContext  # noqa: PLC0415

        repo = getattr(self, "_cost_forecast_repo", None)
        if repo is None or exc.forecast_id is None:
            return
        try:
            forecast = await repo.get(exc.forecast_id)
            if forecast is None:
                return
            updated = forecast.model_copy(
                update={
                    "halt_context": HaltContext(
                        accumulated_cost=exc.accumulated_cost,
                        ceiling_amount=exc.ceiling_amount,
                        currency=exc.currency,
                        halted_at=datetime.now(UTC),
                    ),
                    "updated_at": datetime.now(UTC),
                },
            )
            await repo.save(updated)
        except Exception as stamp_exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(stamp_exc)
            logger.warning(
                EXECUTION_ENGINE_BUDGET_STOPPED,
                task_id=task_id,
                note="forecast halt stamp failed; banner will not surface",
                error_type=type(stamp_exc).__name__,
                error=safe_error_description(stamp_exc),
            )
            return
        logger.debug(
            BUDGET_HARD_CEILING_HALT_STAMPED,
            task_id=task_id,
            forecast_id=str(exc.forecast_id),
            accumulated_cost=exc.accumulated_cost,
            ceiling_amount=exc.ceiling_amount,
        )
