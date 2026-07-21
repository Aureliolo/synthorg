# module-kind: adapter
"""The OpenHands adapter: a 4th ``ExecutionLoop``.

Drives an OpenHands conversation through the injected factory, maps its
event stream to ``TurnRecord``s, and consults the budget / shutdown /
cancellation checkers at each event boundary, stopping the run (via the
sink's ``False`` return) when any trips. Completion honours the same
``NO_OP`` / ``artifacts_expected`` rule as the native loops. All logic is
independent of the SDK, which lives behind the conversation factory.
"""

from dataclasses import dataclass, field
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_helpers import build_result
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
    TurnObserver,
)
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.engine.openhands.conversation import (
    OpenHandsOutcome,
    OpenHandsRunSpec,
)
from synthorg.engine.openhands.errors import (
    OpenHandsLoopError,
    OpenHandsUnavailableError,
)
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.execution.turn import TurnRecord
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import EXECUTION_LOOP_ERROR
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)

_LOOP_TYPE: Final[str] = "openhands"
_CONTAINER_WORKSPACE: Final[str] = "/workspace"
_NO_OP_MESSAGE: Final[str] = (
    "OpenHands run produced no artifacts for an artifact-expecting task"
)


@dataclass
class _RunState:
    """Mutable per-run accumulator threaded through the event sink."""

    ctx: AgentContext
    turns: list[TurnRecord] = field(default_factory=list)
    turn_index: int = 0
    termination: TerminationReason | None = None
    error_message: str | None = None


class OpenHandsLoop:
    """Runs a task through the OpenHands coding agent as an ExecutionLoop.

    Args:
        config: Frozen, settings-driven behaviour.
        deps: Injected collaborators (conversation factory, signer, URLs, clock).
    """

    def __init__(self, *, config: OpenHandsLoopConfig, deps: OpenHandsLoopDeps) -> None:
        self._config = config
        self._deps = deps

    def get_loop_type(self) -> str:
        """Return the loop discriminator.

        Returns:
            The string ``"openhands"``.
        """
        return _LOOP_TYPE

    async def execute(  # noqa: PLR0913 -- ExecutionLoop protocol surface
        self,
        *,
        context: AgentContext,
        provider: CompletionProvider,
        tool_invoker: ToolInvokerProtocol | None = None,
        budget_checker: BudgetChecker | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        completion_config: CompletionConfig | None = None,
        task_cancellation_checker: TaskCancellationChecker | None = None,
        turn_observer: TurnObserver | None = None,
    ) -> ExecutionResult:
        """Run the task through OpenHands and return an ExecutionResult.

        ``provider`` / ``tool_invoker`` / ``completion_config`` are unused:
        OpenHands runs its own LLM (through the gateway) and its own tools
        (native + credentialed-MCP).

        Returns:
            The terminal :class:`ExecutionResult` with mapped ``TurnRecord``s.
        """
        # OpenHands runs its own LLM (via the gateway) and tools (native + MCP).
        del provider, tool_invoker, completion_config
        state = _RunState(ctx=context)
        spec = self._build_spec(context)

        async def sink(event: OpenHandsEvent) -> bool:
            return await self._handle_event(
                event,
                state,
                budget_checker=budget_checker,
                shutdown_checker=shutdown_checker,
                task_cancellation_checker=task_cancellation_checker,
                turn_observer=turn_observer,
            )

        conversation = await self._deps.build_conversation(spec, sink)
        try:
            outcome = await conversation.run()
        except OpenHandsLoopError as exc:
            logger.warning(
                EXECUTION_LOOP_ERROR,
                loop_type=_LOOP_TYPE,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return build_result(
                state.ctx,
                TerminationReason.ERROR,
                state.turns,
                error_message=safe_error_description(exc),
            )
        return self._finalize(state, outcome)

    def _build_spec(self, context: AgentContext) -> OpenHandsRunSpec:
        """Mint a run token and assemble the run spec.

        Returns:
            The :class:`OpenHandsRunSpec` for this run.

        Raises:
            OpenHandsUnavailableError: If the gateway / MCP endpoints are
                unconfigured (the loop cannot reach its governance boundaries).
        """
        if not self._deps.gateway_base_url or not self._deps.mcp_base_url:
            msg = "OpenHands loop needs the gateway and credentialed-MCP URLs set"
            raise OpenHandsUnavailableError(msg)
        model = context.identity.model
        task = context.task_execution.task if context.task_execution else None
        project_id = str(task.project) if task is not None and task.project else None
        token = mint_run_token(
            self._deps.signer,
            execution_id=context.execution_id,
            agent_id=str(context.identity.id),
            task_id=str(task.id) if task is not None else context.execution_id,
            ref=ModelRef(provider=model.provider, model_id=model.model_id),
            project_id=project_id,
            cost_ceiling=context.cost_ceiling,
            ttl_seconds=self._config.token_ttl_seconds,
        )
        return OpenHandsRunSpec(
            task_prompt=_task_prompt(context),
            model=model.model_id,
            gateway_base_url=self._deps.gateway_base_url,
            gateway_token=token,
            mcp_base_url=self._deps.mcp_base_url,
            workspace_path=_CONTAINER_WORKSPACE,
            max_turns=min(context.max_turns, self._config.max_turns),
        )

    async def _handle_event(  # noqa: PLR0913 -- boundary-check surface
        self,
        event: OpenHandsEvent,
        state: _RunState,
        *,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        task_cancellation_checker: TaskCancellationChecker | None,
        turn_observer: TurnObserver | None,
    ) -> bool:
        """Map one event to state and decide whether to continue.

        Returns:
            ``True`` to continue the run, ``False`` to stop at this boundary.
        """
        if event.kind is OpenHandsEventKind.ERROR:
            state.termination = TerminationReason.ERROR
            state.error_message = event.text or "OpenHands run failed"
            return False
        if event.kind is OpenHandsEventKind.FINISHED:
            if state.termination is None:
                state.termination = TerminationReason.COMPLETED
            return False
        if event.kind is OpenHandsEventKind.OBSERVATION:
            return True
        self._record_turn(event, state)
        await self._fire_observer(turn_observer, state, event)
        return await self._check_boundaries(
            state, budget_checker, shutdown_checker, task_cancellation_checker
        )

    def _record_turn(self, event: OpenHandsEvent, state: _RunState) -> None:
        """Accumulate one LLM turn into the context and turn list."""
        state.turn_index += 1
        usage = TokenUsage(
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            cost=event.cost,
        )
        state.ctx = state.ctx.with_turn_completed(
            usage, ChatMessage(role=MessageRole.ASSISTANT, content=event.text)
        )
        tool_calls = (event.tool_name,) if event.tool_name else ()
        state.turns.append(
            TurnRecord(
                turn_number=state.turn_index,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cost=event.cost,
                tool_calls_made=tool_calls,
                finish_reason=event.finish_reason,
            )
        )

    async def _check_boundaries(
        self,
        state: _RunState,
        budget_checker: BudgetChecker | None,
        shutdown_checker: ShutdownChecker | None,
        task_cancellation_checker: TaskCancellationChecker | None,
    ) -> bool:
        """Consult the checkers; record a terminal reason if any trips.

        Returns:
            ``True`` to continue, ``False`` when a checker terminated the run.
        """
        if shutdown_checker is not None and shutdown_checker():
            state.termination = TerminationReason.SHUTDOWN
            return False
        if budget_checker is not None and budget_checker(state.ctx):
            state.termination = TerminationReason.BUDGET_EXHAUSTED
            return False
        if task_cancellation_checker is not None and await task_cancellation_checker():
            state.termination = TerminationReason.CANCELLED
            return False
        if state.ctx.turn_count >= state.ctx.max_turns:
            state.termination = TerminationReason.MAX_TURNS
            return False
        return True

    async def _fire_observer(
        self,
        turn_observer: TurnObserver | None,
        state: _RunState,
        event: OpenHandsEvent,
    ) -> None:
        """Fire the progress observer; never let it corrupt the run."""
        if turn_observer is None:
            return
        labels = (event.tool_name,) if event.tool_name else ()
        try:
            await turn_observer(state.turn_index, labels)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
            # lint-allow: swallow-ok -- progress observer is a best-effort side channel
            reraise_critical(exc)
            logger.warning(
                EXECUTION_LOOP_ERROR,
                loop_type=_LOOP_TYPE,
                note="turn observer raised",
                error_type=type(exc).__name__,
            )

    def _finalize(self, state: _RunState, outcome: OpenHandsOutcome) -> ExecutionResult:
        """Build the terminal ExecutionResult, applying the NO_OP rule.

        Returns:
            The terminal :class:`ExecutionResult`.
        """
        reason = state.termination
        if reason is None:
            reason = (
                TerminationReason.COMPLETED
                if outcome.finished
                else TerminationReason.ERROR
            )
        if reason is TerminationReason.ERROR:
            message = state.error_message or outcome.error_message or "OpenHands failed"
            return build_result(
                state.ctx, TerminationReason.ERROR, state.turns, error_message=message
            )
        if reason is TerminationReason.COMPLETED and self._is_no_op(state):
            return build_result(
                state.ctx,
                TerminationReason.NO_OP,
                state.turns,
                error_message=_NO_OP_MESSAGE,
            )
        return build_result(state.ctx, reason, state.turns)

    @staticmethod
    def _is_no_op(state: _RunState) -> bool:
        """Return whether a completed run is a fail-loud NO_OP.

        Returns:
            ``True`` when the task expected artifacts, the run made no tool
            calls, and it is not a resumed segment.
        """
        task_exec = state.ctx.task_execution
        if task_exec is None or not task_exec.task.artifacts_expected:
            return False
        if is_resumed_run():
            return False
        return not any(turn.tool_calls_made for turn in state.turns)


def _task_prompt(context: AgentContext) -> str:
    """Derive the task prompt for the harness from the context.

    Returns:
        The task title + description, or the last user message for a chat run.
    """
    if context.task_execution is not None:
        task = context.task_execution.task
        parts = [part for part in (task.title, task.description) if part]
        if parts:
            return "\n\n".join(parts)
    for message in reversed(context.conversation):
        if message.role is MessageRole.USER and message.content:
            return message.content
    return "Complete the assigned task."
