"""Approval-resume mixin for :class:`AgentEngine`.

Continues a parked :class:`AgentContext` after a human approval
decision. The parked context is restored by ``ApprovalGate`` on the
decision side; this mixin re-enters the execution loop with the
decision injected, so the agent picks the original work back up
exactly where it left off (design D21 / Park-Resume).
"""

from typing import TYPE_CHECKING

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.prompt import SystemPrompt, build_system_prompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability import get_logger
from synthorg.observability.correlation import correlation_scope
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_COMPLETED,
    APPROVAL_GATE_RESUME_FAILED,
    APPROVAL_GATE_RESUME_STARTED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.tools.protocol import ToolInvokerProtocol

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.core.clock import Clock
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_callables import (
        Execute,
        HandleBudgetError,
        HandleFatalError,
        MakeToolInvoker,
    )
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


class AgentEngineResumeMixin:
    """Resume a parked context after an approval decision.

    Design D21 prescribes returning the approval decision as a
    ``ToolResult``. The implemented park point appends the escalated
    call's tool result *before* the park check (see
    :func:`synthorg.engine.loop_tool_execution.execute_tool_calls`),
    so the parked conversation already answers that ``tool_call_id``;
    injecting a second ``ToolResult`` for the same id would duplicate
    it and malform the provider message stream. The decision is
    therefore injected as a follow-up ``SYSTEM`` message
    (``ApprovalGate.build_resume_message``, passed in as
    ``decision_message``), semantically a continuation of the parked
    tool result rather than a competing return value. The conversation
    shape this relies on is locked by
    ``tests/unit/engine/test_loop_helpers_approval.py``.
    """

    _clock: Clock
    _provider: CompletionProvider
    _budget_enforcer: BudgetEnforcer | None
    _make_tool_invoker: MakeToolInvoker
    _execute: Execute
    _handle_fatal_error: HandleFatalError
    _handle_budget_error: HandleBudgetError

    async def resume_parked_run(
        self,
        *,
        parked_context: AgentContext,
        approval_id: str,
        decision_message: str,
        effective_autonomy: EffectiveAutonomy | None = None,
        timeout_seconds: float | None = None,
    ) -> AgentRunResult:
        """Continue a restored parked context with the decision injected.

        Args:
            parked_context: The deserialized ``AgentContext`` restored
                by ``ApprovalGate.resume_context``.
            approval_id: The approval item identifier (audit context).
            decision_message: The decision text built by
                ``ApprovalGate.build_resume_message`` (already encodes
                APPROVED/REJECTED, decider, and any reason).
            effective_autonomy: Autonomy level governing the resumed
                tool invoker, or ``None`` to leave the rule engine
                governing without the autonomy-tier layer.
            timeout_seconds: Optional wall-clock bound on the resumed
                run.

        Returns:
            The terminal ``AgentRunResult`` of the resumed execution.

        Raises:
            ExecutionStateError: If the parked context carries no
                ``task_execution`` (a parked agent must be task-bound).
        """
        ctx = parked_context
        identity = ctx.identity
        if ctx.task_execution is None:
            msg = (
                f"Parked context for approval {approval_id!r} has no "
                f"task_execution; a parked agent must be task-bound"
            )
            logger.error(
                APPROVAL_GATE_RESUME_FAILED,
                approval_id=approval_id,
                note=msg,
            )
            raise ExecutionStateError(msg)
        task = ctx.task_execution.task
        agent_id = str(identity.id)
        task_id = str(task.id)

        with correlation_scope(
            agent_id=agent_id,
            task_id=task_id,
            project_id=task.project,
        ):
            start = self._clock.monotonic()
            logger.info(
                APPROVAL_GATE_RESUME_STARTED,
                approval_id=approval_id,
                agent_id=agent_id,
                task_id=task_id,
                note="resuming parked context",
            )
            ctx = ctx.with_message(
                ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=decision_message,
                ),
            )
            tool_invoker, system_prompt = self._build_resume_runtime(
                identity,
                task,
                task_id=task_id,
                effective_autonomy=effective_autonomy,
            )
            return await self._resume_execute(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                approval_id=approval_id,
                ctx=ctx,
                system_prompt=system_prompt,
                tool_invoker=tool_invoker,
                effective_autonomy=effective_autonomy,
                start=start,
                timeout_seconds=timeout_seconds,
            )

    def _build_resume_runtime(
        self,
        identity: AgentIdentity,
        task: Task,
        *,
        task_id: str,
        effective_autonomy: EffectiveAutonomy | None,
    ) -> tuple[ToolInvokerProtocol | None, SystemPrompt]:
        """Build the resumed run's tool invoker and system prompt.

        Extracted from :meth:`resume_parked_run` so that method stays
        focused on context restoration + decision injection. The
        system prompt is rebuilt (not restored) because it is
        deterministic from identity/task and the original is already
        present verbatim in the restored conversation; rebuilding here
        avoids re-firing the personality-trim notification a fresh
        ``_prepare_context`` would.

        Returns:
            ``(tool_invoker, system_prompt)``: the per-resume invoker
            and the freshly-built system prompt for the resumed loop.
        """
        tool_invoker = self._make_tool_invoker(
            identity,
            task_id=task_id,
            effective_autonomy=effective_autonomy,
            project_id=task.project,
        )
        currency = (
            self._budget_enforcer.currency
            if self._budget_enforcer is not None
            else DEFAULT_CURRENCY
        )
        system_prompt = build_system_prompt(
            agent=identity,
            task=task,
            l1_summaries=(tool_invoker.get_l1_summaries() if tool_invoker else ()),
            effective_autonomy=effective_autonomy,
            currency=currency,
            model_tier=identity.model.model_tier,
        )
        return tool_invoker, system_prompt

    async def _resume_execute(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        approval_id: str,
        ctx: AgentContext,
        system_prompt: SystemPrompt,
        tool_invoker: ToolInvokerProtocol | None,
        effective_autonomy: EffectiveAutonomy | None,
        start: float,
        timeout_seconds: float | None,
    ) -> AgentRunResult:
        """Run the resumed loop, mirroring ``run()``'s error handling.

        Budget / fatal errors are funnelled through the same handlers
        ``run()`` uses so a failed resume still syncs an authoritative
        terminal task state to the ``TaskEngine`` instead of leaving
        the task stuck mid-flight.

        Returns:
            The terminal :class:`AgentRunResult` of the resumed run
            (a budget / fatal handler may rewrite the termination
            reason to ``BUDGET_EXHAUSTED`` / ``ERROR``).
        """
        try:
            result = await self._execute(
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                completion_config=None,
                ctx=ctx,
                system_prompt=system_prompt,
                start=start,
                timeout_seconds=timeout_seconds,
                tool_invoker=tool_invoker,
                effective_autonomy=effective_autonomy,
                provider=self._provider,
            )
        except BudgetExhaustedError as exc:
            return await self._handle_budget_error(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                duration_seconds=self._clock.monotonic() - start,
                ctx=ctx,
                system_prompt=system_prompt,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return await self._handle_fatal_error(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                duration_seconds=self._clock.monotonic() - start,
                ctx=ctx,
                system_prompt=system_prompt,
                effective_autonomy=effective_autonomy,
                provider=self._provider,
            )
        logger.info(
            APPROVAL_GATE_RESUME_COMPLETED,
            approval_id=approval_id,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
        )
        return result
