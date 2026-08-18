# module-kind: adapter
"""The OpenHands adapter: the bundled ``ExecutionLoop``.

Drives an OpenHands conversation through the injected factory, maps its
event stream to ``TurnRecord``s, and consults the budget / shutdown /
cancellation checkers at each turn boundary (after recording a turn event),
stopping the run (via the sink's ``False`` return) when any trips.
Completion honours the same ``NO_OP`` / ``artifacts_expected`` rule as the
native loops. All logic is independent of the SDK, which lives behind the
conversation factory.
"""

from dataclasses import dataclass, field
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_empty_run import delivered_nothing
from synthorg.engine.loop_helpers import build_result
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TaskCancellationChecker,
    TerminationReason,
    TurnObserver,
    TurnProgress,
)
from synthorg.engine.loop_unresolved_tools import unresolved_tools_result
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.engine.openhands.conversation import (
    OpenHandsOutcome,
    OpenHandsRunSpec,
)
from synthorg.engine.openhands.errors import OpenHandsLoopError
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind
from synthorg.engine.prompt_result import without_tool_catalogue
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.execution.turn import TurnRecord
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_TERMINATED,
    EXECUTION_LOOP_TOOL_REJECTED,
    EXECUTION_MAX_TURNS_EXCEEDED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)

_LOOP_TYPE: Final[str] = "openhands"
_CONTAINER_WORKSPACE: Final[str] = "/workspace"
# Namespace for deriving a stable conversation UUID from a non-UUID id.
_CONVERSATION_ID_NAMESPACE: Final[UUID] = uuid5(
    NAMESPACE_URL, "https://synthorg.io/openhands/conversation"
)
_NO_OP_MESSAGE: Final[str] = (
    "OpenHands run produced no artifacts for an artifact-expecting task"
)


def _stable_conversation_id(raw: str) -> UUID:
    """Return a stable UUID for a task / execution id.

    ``task.id`` is already a UUID, but the ``execution_id`` fallback is only a
    ``NotBlankStr``. Deriving a deterministic ``uuid5`` for a non-UUID id keeps
    the conversation-reattach key stable across resumes while guaranteeing the
    run spec's ``UUID`` conversation id is always well-formed.

    Returns:
        The canonical UUID used as the run's conversation id.
    """
    try:
        return UUID(raw)
    except ValueError:
        return uuid5(_CONVERSATION_ID_NAMESPACE, raw)


def _record_resolution(turns: list[TurnRecord]) -> None:
    """Credit the last turn with one tool call that resolved.

    The harness runs one call at a time and reports each result as it lands,
    so an observation names the call the most recent turn asked for. Capped at
    what that turn asked for, because a count above it would describe a turn
    that never happened.
    """
    if not turns:
        return
    last = turns[-1]
    resolved = min(last.resolved_tool_calls + 1, len(last.tool_calls_made))
    turns[-1] = last.model_copy(update={"resolved_tool_calls": resolved})


@dataclass
class _RunState:
    """Mutable per-run accumulator threaded through the event sink."""

    ctx: AgentContext
    turns: list[TurnRecord] = field(default_factory=list)
    turn_index: int = 0
    termination: TerminationReason | None = None
    error_message: str | None = None
    # A terminal result the shared decision built for itself, carrying the
    # metadata and the log line that go with it; returned as it stands.
    settled: ExecutionResult | None = None


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
        streaming_enabled: bool = False,
    ) -> ExecutionResult:
        """Run the task through OpenHands and return an ExecutionResult.

        ``provider`` / ``tool_invoker`` / ``streaming_enabled`` are unused:
        OpenHands runs its own LLM (through the gateway, which owns its own
        streaming + cost) and its own tools (native + credentialed-MCP).
        ``completion_config`` is not: its sampling half travels into the run
        spec, because the harness choosing its own temperature while the native
        loop is handed one is a difference between the loops that nobody chose.

        Returns:
            The terminal :class:`ExecutionResult` with mapped ``TurnRecord``s.
        """
        # OpenHands runs its own LLM (via the gateway) and tools (native + MCP).
        del provider, tool_invoker, streaming_enabled
        # Continued from the context, not restarted: a resumed run arrives with
        # turns already on its conversation, and numbering the next one 1 gives
        # the recorder a second turn 1 for the same execution. The frames are
        # keyed on that index, so the pairing a replay depends on comes apart
        # exactly on the runs that were interrupted.
        state = _RunState(ctx=context, turn_index=context.turn_count)
        spec = self._build_spec(context, completion_config)

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

    def _build_spec(
        self, context: AgentContext, completion_config: CompletionConfig | None
    ) -> OpenHandsRunSpec:
        """Mint a run token and assemble the run spec.

        The gateway / MCP URLs are guaranteed non-blank by
        :class:`OpenHandsLoopDeps` construction (the wiring returns ``None``
        deps rather than constructing with blank URLs, and a ``None`` deps
        fails loud at loop build). ``conversation_id`` is a stable UUID derived
        from the task id so a resumed run re-attaches to the persisted
        conversation and the container's ``UUID(...)`` parse cannot raise.

        The sampling fields travel from the run's own ``CompletionConfig``, the
        one the native loop samples on. The SDK leaves every sampling knob
        unset for its caller to fill, so an adapter that drops the config sends
        none and the provider decides: the same brief then runs at one
        temperature under one loop and another under the other.

        Returns:
            The :class:`OpenHandsRunSpec` for this run.

        Raises:
            GatewayModelUnboundError: If the agent's model is unbound (a
                systemic misconfiguration surfaced fail-loud, not a per-run
                error).
        """
        model = context.identity.model
        task = context.task_execution.task if context.task_execution else None
        project_id = str(task.project) if task is not None and task.project else None
        task_id = str(task.id) if task is not None else context.execution_id
        token = mint_run_token(
            self._deps.signer,
            execution_id=context.execution_id,
            agent_id=str(context.identity.id),
            task_id=task_id,
            ref=ModelRef(provider=model.provider, model_id=model.model_id),
            project_id=project_id,
            cost_ceiling=context.cost_ceiling,
            ttl_seconds=self._config.token_ttl_seconds,
        )
        return OpenHandsRunSpec(
            task_prompt=_task_prompt(context),
            system_prompt=_system_prompt(context),
            model=model.model_id,
            gateway_base_url=self._deps.gateway_base_url,
            gateway_token=token,
            mcp_base_url=self._deps.mcp_base_url,
            workspace_path=_CONTAINER_WORKSPACE,
            conversation_id=_stable_conversation_id(task_id),
            max_turns=min(context.max_turns, self._config.max_turns),
            temperature=completion_config.temperature if completion_config else None,
            max_output_tokens=(
                completion_config.max_tokens if completion_config else None
            ),
            top_p=completion_config.top_p if completion_config else None,
            project_id=project_id,
        )

    async def _handle_event(
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
        if event.kind is OpenHandsEventKind.TOOL_ERROR:
            # Not terminal: the harness hands this back to the model as an
            # observation so the next turn can fix the call. Ending the run
            # here spends a whole repetition on a misspelt argument, and the
            # native loop returns the same class of error to its own model and
            # carries on, so a shared scoreboard would be reading one loop's
            # recovery against the other's execution.
            logger.warning(
                EXECUTION_LOOP_TOOL_REJECTED,
                loop_type=_LOOP_TYPE,
                execution_id=state.ctx.execution_id,
                tool_name=event.tool_name,
            )
            # Not terminal per call, but a run doing only this is going
            # nowhere, and the ceiling that says when belongs to the operator
            # and covers both loops. The turn stays at zero resolved calls,
            # which is what the streak reads.
            state.settled = unresolved_tools_result(state.ctx, state.turns)
            return state.settled is None
        if event.kind is OpenHandsEventKind.ERROR:
            state.termination = TerminationReason.ERROR
            state.error_message = event.text or "OpenHands run failed"
            logger.warning(
                EXECUTION_LOOP_ERROR,
                loop_type=_LOOP_TYPE,
                note="OpenHands emitted an error event",
            )
            return False
        if event.kind is OpenHandsEventKind.FINISHED:
            if state.termination is None:
                state.termination = TerminationReason.COMPLETED
            return False
        if event.kind is OpenHandsEventKind.OBSERVATION:
            # The tool ran and returned: the only point at which this loop
            # learns that the call the last turn asked for resolved.
            _record_resolution(state.turns)
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
                # Nothing has run yet: this event is the model asking. What
                # resolved arrives afterwards, as the observation or the
                # rejection, which is where the count is filled in. The native
                # loop records the same fact in the same order, from its tool
                # results rather than from an event.
                resolved_tool_calls=0,
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
            # This loop reaches its ceiling here rather than through
            # ``ceiling_result``, so it owes the same fact the native loop
            # emits there: the scorers key on the event name, and a
            # termination reason alone reaches none of them.
            logger.warning(
                EXECUTION_MAX_TURNS_EXCEEDED,
                execution_id=state.ctx.execution_id,
                max_turns=state.ctx.max_turns,
                turn_count=state.ctx.turn_count,
            )
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
            await turn_observer(TurnProgress(state.turn_index, labels, state.ctx))
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
        if state.settled is not None:
            # Already terminal, and already logged by the decision that built
            # it. Rebuilding from the reason alone would drop the metadata
            # naming what the run kept asking for, which is the finding.
            return state.settled
        reason = state.termination
        if reason is None:
            reason = (
                TerminationReason.COMPLETED
                if outcome.finished
                else TerminationReason.ERROR
            )
        if reason is TerminationReason.ERROR:
            message = state.error_message or outcome.error_message or "OpenHands failed"
            self._log_terminated(state, TerminationReason.ERROR)
            return build_result(
                state.ctx, TerminationReason.ERROR, state.turns, error_message=message
            )
        if reason is TerminationReason.COMPLETED and self._is_no_op(state):
            self._log_terminated(state, TerminationReason.NO_OP)
            return build_result(
                state.ctx,
                TerminationReason.NO_OP,
                state.turns,
                error_message=_NO_OP_MESSAGE,
            )
        self._log_terminated(state, reason)
        return build_result(state.ctx, reason, state.turns)

    @staticmethod
    def _log_terminated(state: _RunState, reason: TerminationReason) -> None:
        """Log the loop's terminal transition, mirroring the sibling loops."""
        logger.info(
            EXECUTION_LOOP_TERMINATED,
            loop_type=_LOOP_TYPE,
            reason=reason.value,
            turns=len(state.turns),
        )

    @staticmethod
    def _is_no_op(state: _RunState) -> bool:
        """Return whether a completed run is a fail-loud NO_OP.

        The predicate is the native loop's, deliberately: a run judged empty
        under one loop and productive under the other would be a difference
        between the adapters rather than between the runs.

        Returns:
            ``True`` when the task expected artifacts, the run called nothing
            that could deliver one, and it is not a resumed segment.
        """
        task_exec = state.ctx.task_execution
        if task_exec is None or not task_exec.task.artifacts_expected:
            return False
        if is_resumed_run():
            return False
        return delivered_nothing(state.turns)


def _system_prompt(context: AgentContext) -> str | None:
    """Return the system prompt the engine built for this run.

    The engine puts it at the head of the conversation before any loop runs, so
    it is read from there rather than rebuilt: the two loops then answer the
    same brief, and a difference in the scoreboard is a difference between the
    loops rather than between what each was told.

    Its tool catalogue is the exception, and is dropped. The harness holds its
    own tools and discloses them itself, so inheriting a catalogue built for
    the native invoker tells the model that tools it cannot reach are the only
    ones that exist. It then calls one and the run dies on the first turn.

    Returns:
        The system message's content without the tool catalogue, or ``None``
        when the context carries no system message.
    """
    for message in context.conversation:
        if message.role is MessageRole.SYSTEM and message.content:
            return without_tool_catalogue(message.content)
    return None


def _task_prompt(context: AgentContext) -> str:
    """Derive the task prompt for the harness from the context.

    A task's title and description are client-supplied free text, and this is
    the message that drives the harness's agent loop, so it is fenced the same
    way the system prompt fences the same two fields. Sending it raw would put
    the identical content in front of the model twice, once sealed as data and
    once as the instruction it is being asked to carry out, which reads as a
    stronger directive than the fenced copy it contradicts.

    The chat branch below needs no fence: the engine already appended that
    message wrapped.

    Returns:
        The task title + description, or the last user message for a chat run.
    """
    if context.task_execution is not None:
        task = context.task_execution.task
        parts = [part for part in (task.title, task.description) if part]
        if parts:
            return wrap_untrusted(TAG_TASK_DATA, "\n\n".join(parts))
    for message in reversed(context.conversation):
        if message.role is MessageRole.USER and message.content:
            return message.content
    return "Complete the assigned task."
