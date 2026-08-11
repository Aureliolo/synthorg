# module-kind: service
"""Budget-halt handling for :class:`AgentEngine`.

A ceiling crossing is a controlled stop, not a crash: the run parks so an
operator can raise the ceiling and resume, and every step of that park is
best-effort so a persistence problem degrades to a plain budget stop rather
than escalating into an engine failure.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.budget.errors import (
    BudgetExhaustedError,
    RunHardCeilingExceededError,
    RunHardTokenCeilingExceededError,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
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
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

logger = get_logger(__name__)

#: The two ceilings that park a run rather than stopping it. Money and
#: tokens are separate errors because their payloads are: a halt context
#: claiming a currency for a token count would read true and be false.
#: One declaration, used both as the annotation and as the runtime check
#: (``isinstance`` accepts a union since 3.10), so a third ceiling cannot
#: be added to one and missed by the other.
type _CeilingError = RunHardCeilingExceededError | RunHardTokenCeilingExceededError


def _ceiling_reason(exc: _CeilingError) -> str:
    """Render the operator-facing reason for a parked ceiling crossing.

    Names the unit, the ceiling, what was accumulated, and (for tokens) the
    two knobs that raise it, because there is no forecast row for the
    operator to raise a token ceiling through. Both knobs are writable: the
    task's own bound through ``PATCH /tasks/{id}`` and the global through
    the settings surface. Naming one the operator cannot reach would park
    the run behind an instruction that does nothing.

    Returns:
        The parked approval's reason text.
    """
    if isinstance(exc, RunHardTokenCeilingExceededError):
        return (
            f"Run hard token ceiling exceeded: accumulated {exc.tokens_used}"
            f" tokens >= ceiling {exc.token_ceiling}. Raise this task's"
            f" hard_token_ceiling (PATCH /tasks/{{id}}) or the global"
            f" budget.run_hard_token_ceiling, then resume this run."
        )
    return (
        f"Run hard ceiling exceeded: accumulated"
        f" {exc.accumulated_cost:.4f} {exc.currency}"
        f" >= ceiling {exc.ceiling_amount:.4f} {exc.currency}"
    )


class AgentEngineBudgetHaltMixin:
    """Mixin turning a budget exhaustion into a parked or stopped run."""

    # Declared, not probed with getattr. A rename on the host would leave a
    # getattr read silently returning None, which routes a genuine
    # hard-ceiling park to BUDGET_EXHAUSTED with nothing to notice it.
    _approval_gate: ApprovalGate | None
    _cost_tracker: CostTrackerProtocol | None
    _cost_forecast_repo: CostForecastRepository | None
    _clock: Clock

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
        to persist the parked state. Anything that stops the park from
        being persisted, an absent repository included, degrades to
        BUDGET_EXHAUSTED (the existing controlled-stop path) rather
        than crashing the engine or reporting a park no resume could
        find. All other ``BudgetExhaustedError`` subclasses
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
        has_gate = self._approval_gate is not None
        parked_ok = False
        is_ceiling = False
        if isinstance(
            exc, RunHardCeilingExceededError | RunHardTokenCeilingExceededError
        ):
            is_ceiling = True
            if has_gate:
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
                currency=resolve_tracker_currency(self._cost_tracker),
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
        exc: _CeilingError,
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
        engine even if the persistence layer is in a bad state. An
        absent repository is one of those failures rather than a quiet
        success, so the run stops where the operator can see it instead
        of waiting on an approval whose context was never written.

        Returns:
            ``True`` when :py:meth:`ApprovalGate.park_context` succeeds,
            ``False`` on any failure (no gate, persistence error, missing
            repo). The forecast halt stamp that follows is a read-side
            marker only, so its outcome does not change this answer: the
            run is parked either way, and reporting otherwise would route
            a genuinely parked run to BUDGET_EXHAUSTED.
        """
        from synthorg.approval.enums import ApprovalRiskLevel  # noqa: PLC0415
        from synthorg.approval.models import (  # noqa: PLC0415
            EscalationInfo,
        )

        gate = self._approval_gate
        if gate is None:
            return False
        try:
            forecast_id_str = (
                str(exc.forecast_id)
                if isinstance(exc, RunHardCeilingExceededError)
                and exc.forecast_id is not None
                else "no-forecast"
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
                # One action type for both units. It is the same event, "a run
                # halted on a ceiling"; a second would be a fresh entry in the
                # autonomy taxonomy for no gain, and the reason below already
                # says which ceiling and how to raise it.
                action_type="budget:hard_ceiling_exceeded",
                risk_level=ApprovalRiskLevel.HIGH,
                reason=_ceiling_reason(exc),
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
        exc: _CeilingError,
        *,
        task_id: str,
    ) -> None:
        """Record halt context on the forecast row so the UI can resume.

        Money only. ``HaltContext`` hangs off a ``cost_forecasts`` row
        whose columns are money and a timestamp, and a forecast estimates
        money: it has nothing to say about a token count, and a halt
        context claiming a currency for one would be a record that reads
        true and is not. A token crossing is raised and resumed through
        ``budget.run_hard_token_ceiling`` or the task's own override, which
        the parked approval's reason names.

        Best-effort: a missing repo, a missing forecast id, or any repo
        error degrades silently. The park itself already succeeded; a
        failure to stamp the read-side halt marker must never turn a
        clean ceiling halt into a crash.
        """
        from synthorg.budget.forecast_models import HaltContext  # noqa: PLC0415

        if not isinstance(exc, RunHardCeilingExceededError):
            return
        repo = self._cost_forecast_repo
        if repo is None or exc.forecast_id is None:
            return
        # One instant for both fields, read through the engine's own seam so
        # a test with a FakeClock sees the halt stamped at the time it set.
        stamped_at = self._clock.now()
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
                        halted_at=stamped_at,
                    ),
                    "updated_at": stamped_at,
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
