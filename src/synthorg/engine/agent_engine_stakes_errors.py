# module-kind: service
"""Stakes-tier escalation handling for :class:`AgentEngine`.

When the agent holding a consequential task runs below the rung that task
demands, and its stakes refuse a weaker one, dispatch raises
:class:`StakesModelUnavailableError`. This mixin escalates that: it parks
the run for an operator decision when an approval gate is wired, and
otherwise degrades to the fatal-error boundary so the task terminates
``FAILED``. The answer is an agent at the needed rung, never a stronger
model behind this agent's name, so a consequential task is neither silently
run under-capable nor silently upgraded.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import resolve_tracker_currency
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.prompt import SystemPrompt, build_error_prompt
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR
from synthorg.observability.events.stakes_routing import STAKES_ROUTING_ESCALATED
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_callables import HandleFatalError
    from synthorg.engine.approval_gate import ApprovalGate

logger = get_logger(__name__)


class AgentEngineStakesErrorsMixin:
    """Mixin routing a stakes-tier miss to PARKED (gated) or FAILED."""

    _approval_gate: ApprovalGate | None
    _cost_tracker: CostTrackerProtocol | None
    _handle_fatal_error: HandleFatalError

    async def _handle_stakes_unavailable(  # noqa: PLR0913
        self,
        *,
        exc: StakesModelUnavailableError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
    ) -> AgentRunResult:
        """Escalate a stakes tier failure: park when a gate is wired, else fail.

        The agent holding this task runs below the rung its stakes demand.
        When an approval gate is wired the run is parked for an operator
        decision (staff an agent bound to a stronger model, or lower the
        stakes) and a ``PARKED`` result is returned; otherwise the failure
        falls through to the fatal-error boundary, terminating the task
        ``FAILED``. The task is never silently run under-capable.

        Returns:
            A ``PARKED`` :class:`AgentRunResult` when the run was parked, or
            the ``FAILED`` result from :meth:`_handle_fatal_error` otherwise.
        """
        has_gate = getattr(self, "_approval_gate", None) is not None
        if has_gate:
            # Build (and validate) the parked result before the park is
            # persisted, so a build failure degrades cleanly to FAILED with no
            # orphaned parked context.
            parked_result = self._build_stakes_parked_result(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                system_prompt=system_prompt,
                duration_seconds=duration_seconds,
                ctx=ctx,
            )
            if parked_result is not None and await self._park_stakes_unavailable(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                ctx=ctx,
            ):
                # Emitted only once the park has persisted: a run that fell
                # through to FAILED was not escalated.
                logger.warning(
                    STAKES_ROUTING_ESCALATED,
                    agent_id=agent_id,
                    task_id=task_id,
                    stakes=exc.stakes.value,
                    required_capability=exc.required_capability,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return parked_result
        return await self._handle_fatal_error(
            exc=exc,
            identity=identity,
            task=task,
            agent_id=agent_id,
            task_id=task_id,
            duration_seconds=duration_seconds,
            ctx=ctx,
            system_prompt=system_prompt,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
            provider=provider,
        )

    def _build_stakes_parked_result(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        system_prompt: SystemPrompt | None,
        duration_seconds: float,
        ctx: AgentContext | None,
    ) -> AgentRunResult | None:
        """Assemble the PARKED result before the park is persisted.

        Building (and thereby validating) the result before
        :meth:`_park_stakes_unavailable` writes anything means a build failure
        degrades cleanly to FAILED with no orphaned parked context.

        Returns:
            The ``PARKED`` :class:`AgentRunResult`, or ``None`` when assembly
            failed (the caller then degrades to the fatal-error boundary).
        """
        try:
            error_ctx = ctx or AgentContext.from_identity(identity, task=task)
            parked_result = ExecutionResult(
                context=error_ctx,
                termination_reason=TerminationReason.PARKED,
            )
            return AgentRunResult(
                execution_result=parked_result,
                system_prompt=build_error_prompt(identity, agent_id, system_prompt),
                duration_seconds=duration_seconds,
                agent_id=agent_id,
                task_id=task_id,
                currency=resolve_tracker_currency(
                    getattr(self, "_cost_tracker", None),
                ),
            )
        except Exception as build_exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- a parked-result build failure degrades to
            # _handle_fatal_error (FAILED); the original stakes error surfaces.
            reraise_critical(build_exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(build_exc).__name__,
                error=safe_error_description(build_exc),
                stage="build_stakes_parked_result",
            )
            return None

    async def _park_stakes_unavailable(
        self,
        *,
        exc: StakesModelUnavailableError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        ctx: AgentContext | None,
    ) -> bool:
        """Park a stakes-unavailable escalation for an operator decision.

        Best-effort: no gate, a serialization error, or a persistence error
        returns ``False`` so the caller degrades to the FAILED path. A failure
        to park must not itself crash the engine.

        Returns:
            ``True`` when the context was parked, ``False`` on any failure.
        """
        from synthorg.approval.enums import ApprovalRiskLevel  # noqa: PLC0415
        from synthorg.approval.models import EscalationInfo  # noqa: PLC0415

        gate: ApprovalGate | None = getattr(self, "_approval_gate", None)
        if gate is None:
            return False
        try:
            if exc.unresolved:
                reason = (
                    f"This agent's bound model ({identity.model.provider}/"
                    f"{identity.model.model_id}) carries no capability grade, "
                    f"so it cannot be measured against the "
                    f"{exc.required_capability} capability that "
                    f"{exc.stakes.value}-stakes work requires. Grade that "
                    f"model, or bind the agent to one already graded."
                )
            else:
                reason = (
                    f"This agent runs below the {exc.required_capability} "
                    f"capability that {exc.stakes.value}-stakes work requires. "
                    f"Staff an agent bound to a stronger model, or lower the "
                    f"task's stakes."
                )
            escalation = EscalationInfo(
                approval_id=f"stakes-unavailable-{task_id}-{uuid4().hex[:12]}",
                tool_call_id=f"capability-policy-{task_id}",
                tool_name="capability_policy",
                action_type="stakes:model_unavailable",
                risk_level=ApprovalRiskLevel.HIGH,
                reason=reason,
            )
            park_ctx = ctx or AgentContext.from_identity(identity, task=task)
            await gate.park_context(
                escalation=escalation,
                context=park_ctx,
                agent_id=agent_id,
                task_id=task_id,
            )
        except Exception as park_exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(park_exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                note="stakes park_context failed; falling back to FAILED",
                error_type=type(park_exc).__name__,
                error=safe_error_description(park_exc),
            )
            return False
        return True
