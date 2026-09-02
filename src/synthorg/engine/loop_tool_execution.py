"""Tool-call execution helpers for execution loops.

Extracted from :mod:`synthorg.engine.loop_helpers` to keep the main
helpers module under the project size limit.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Self

from pydantic import TypeAdapter, ValidationError

from synthorg.approval.models import EscalationInfo
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.compaction_request_channel import CompactionRequest
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.loop_tool_output_budget import (
    DEFAULT_TOOL_OUTPUT_MAX_CHARS,
    MIN_TOOL_OUTPUT_MAX_CHARS,
    abbreviate_tool_output,
)
from synthorg.engine.loop_tool_result_fencing import wrap_tool_result
from synthorg.execution.turn import TurnRecord
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PARK_TASKLESS,
)
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_AGENT_COMPACTION_REQUESTED,
)
from synthorg.observability.events.execution import (
    EXECUTION_BACKGROUND_JOB_WATCH_STARTED,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_TOOL_CALLS,
    EXECUTION_TOOL_OUTPUT_ABBREVIATED,
)
from synthorg.observability.events.tool import (
    TOOL_L2_LOADED,
    TOOL_L3_FETCHED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    ToolCall,
    ToolResult,
)
from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)

#: The only tool whose result the loop reads for a background job id;
#: named once so the capture branch and any future reference agree.
_SHELL_COMMAND_TOOL_NAME: Final[str] = "shell_command"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolTurnControls:
    """What the loop decided for one tool turn.

    The collaborators a turn needs (the invoker, the approval gate) are
    passed by name; these are the per-turn decisions, grouped so the loop
    hands them over as one value and a new decision joins here rather than
    widening the call.

    Attributes:
        clock: Stamps the background-job watch when one starts.
        watch_background_jobs: Whether a backgrounded shell job is watched.
        tool_output_max_chars: Ceiling on one result's content before it
            enters the conversation; the loop resolves it live each turn.
            Zero is no ceiling; any other value is at least
            ``MIN_TOOL_OUTPUT_MAX_CHARS``, since the abbreviation marker has
            to fit inside it.
    """

    clock: Clock
    watch_background_jobs: bool
    tool_output_max_chars: int

    def __post_init__(self) -> None:
        """Refuse a ceiling the abbreviation could not honour.

        Raises:
            ValueError: The ceiling is positive and below the floor.
        """
        if 0 < self.tool_output_max_chars < MIN_TOOL_OUTPUT_MAX_CHARS:
            msg = (
                f"tool_output_max_chars must be 0 or at least "
                f"{MIN_TOOL_OUTPUT_MAX_CHARS}, got {self.tool_output_max_chars}"
            )
            raise ValueError(msg)

    @classmethod
    def defaults(cls) -> Self:
        """The controls a turn runs under when no loop decided them.

        Returns:
            The wall clock, no job watch, and the registered output ceiling.
        """
        return cls(
            clock=SystemClock(),
            watch_background_jobs=False,
            tool_output_max_chars=DEFAULT_TOOL_OUTPUT_MAX_CHARS,
        )


#: Lax-mode ``bool`` coercion, matching how ``ShellCommandArgs.background``
#: itself validates the same raw tool-call argument (no ``strict=True``
#: there): a model may legally emit ``1`` or ``"true"`` and the tool still
#: backgrounds the job, so the capture gate below must read the argument
#: the same way or it silently never watches a job the tool genuinely ran
#: in the background.
_BOOL_ADAPTER: Final[TypeAdapter[bool]] = TypeAdapter(bool)


def _build_error_result(
    ctx: AgentContext,
    turns: list[TurnRecord],
    error_message: str,
    *,
    metadata: dict[str, object] | None = None,
) -> ExecutionResult:
    """Inline build_result helper avoiding a circular import.

    Returns:
        An ERROR :class:`ExecutionResult` carrying the supplied
        ``error_message`` and optional metadata.
    """
    from synthorg.engine.loop_helpers import build_result  # noqa: PLC0415

    return build_result(
        ctx,
        TerminationReason.ERROR,
        turns,
        error_message=error_message,
        metadata=metadata or {},
    )


def clear_last_turn_tool_calls(turns: list[TurnRecord]) -> None:
    """Clear tool_calls_made on the last TurnRecord.

    Used when shutdown fires between recording a turn and executing
    tools -- the turn should not overstate what happened.
    """
    if turns:
        last = turns[-1]
        turns[-1] = last.model_copy(
            update={
                "tool_calls_made": (),
                "tool_call_fingerprints": (),
                "resolved_tool_calls": 0,
            },
        )


def record_resolved_tool_calls(
    turns: list[TurnRecord],
    results: Sequence[ToolResult],
) -> None:
    """Record how many of the turn's tool calls named a tool that exists.

    The turn record is built from the model's response, before anything runs,
    so it can only say what was asked for. What actually resolved is known
    here, and the turn-budget guard needs it: a run asking for a tool nobody
    registered has called a tool and run nothing, and without this it buys an
    extension for doing so.
    """
    if not turns:
        return
    resolved = sum(1 for result in results if not result.is_unresolved)
    turns[-1] = turns[-1].model_copy(update={"resolved_tool_calls": resolved})


async def _park_for_approval(
    ctx: AgentContext,
    escalation: EscalationInfo,
    approval_gate: ApprovalGate,
    turns: list[TurnRecord],
) -> ExecutionResult:
    """Park the context for approval and return a PARKED or ERROR result.

    Returns:
        An :class:`ExecutionResult` with ``termination_reason=PARKED``
        on successful park, or ``ERROR`` when parking fails (the
        critical-error branch propagates upward).
    """
    from synthorg.engine.loop_helpers import build_result  # noqa: PLC0415

    agent_id = str(ctx.identity.id)
    task_id: str | None = None
    if ctx.task_execution is not None:
        task_id = str(ctx.task_execution.task.id)
    else:
        logger.debug(
            APPROVAL_GATE_PARK_TASKLESS,
            approval_id=escalation.approval_id,
            agent_id=agent_id,
            note="No task_execution on context -- task_id will be None",
        )

    try:
        await approval_gate.park_context(
            escalation=escalation,
            context=ctx,
            agent_id=agent_id,
            task_id=task_id,
            # The AG-UI session is the task, so a parked run surfaces an
            # APPROVAL_INTERRUPT on the same stream the dashboard is watching.
            session_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- returns ERROR result
        reraise_critical(exc)
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=(
                f"Approval escalation detected (id={escalation.approval_id}) "
                f"but context parking failed -- cannot resume"
            ),
            metadata={
                "approval_id": escalation.approval_id,
                "parking_failed": True,
            },
        )

    return build_result(
        ctx,
        TerminationReason.PARKED,
        turns,
        metadata={
            "approval_id": escalation.approval_id,
            "parking_failed": False,
            # Carried so the post-execution pipeline can move the task to
            # AWAITING_INPUT for a clarification park (distinct from the
            # binary approval park, which leaves the task IN_PROGRESS).
            "clarification": escalation.clarification,
        },
    )


def _coerced_bool(raw: object, *, default: bool) -> bool:
    """Coerce a raw tool-call argument to bool, matching Pydantic's lax mode.

    Tool-call arguments read here are the RAW values the model emitted, not
    the validated ones the tool itself parses, so a bool-shaped argument
    (``background``, ``preserve_markers``) may legally arrive as ``1`` or
    ``"true"`` and must be coerced the same way the tool's own arg model
    would, or the loop reads a call the tool accepted as one it declined.

    Returns:
        The coerced value, or *default* when *raw* is absent or not
        coercible.
    """
    if raw is None:
        return default
    try:
        return _BOOL_ADAPTER.validate_python(raw)
    except ValidationError:
        return default


def _parsed_background_job_id(raw_content: str) -> NotBlankStr | None:
    """Extract a ``job_id`` from a successful backgrounded ``shell_command`` result.

    Reads the RAW (unwrapped) result content: ``_append_tool_results``
    builds a separate ``wrapped`` object per result without reassigning
    ``results`` itself, so the tuple this function's caller iterates
    still holds each result's original, unfenced ``content``.

    Best-effort by design: a parse failure or a missing/blank ``job_id``
    means this is not a shape ``shell_command(background=True)`` would
    ever actually return, not a contract the tool must satisfy, so
    nothing is watched rather than raising.

    Returns:
        The job id, or ``None`` when *raw_content* is not a
        ``{"job_id": "..."}`` JSON object.
    """
    try:
        payload = json.loads(raw_content)
    except ValueError, TypeError:
        return None
    if not isinstance(payload, dict):
        return None
    job_id = payload.get("job_id")
    if isinstance(job_id, str) and job_id.strip():
        return NotBlankStr(job_id)
    return None


def _apply_tool_call_side_effect(
    ctx: AgentContext,
    tc: ToolCall,
    result: ToolResult,
    turn_number: int,
    *,
    clock: Clock,
    watch_background_jobs: bool,
) -> AgentContext:
    """Apply one successful tool call's context side effect, if any.

    One arm per tool this loop observes results for: ``load_tool``,
    backgrounded ``shell_command`` (only while *watch_background_jobs* is
    set -- the stall nudge is off by default, and there is no point
    growing ``AgentContext.background_job_watch`` for a run nothing ever
    reads it back for), ``load_tool_resource``, and ``compact_context``
    (recorded from the tool CALL's own arguments, not the tool's result --
    the invoker boundary drops ``ToolExecutionResult.metadata`` before it
    ever reaches the loop). Called only for a
    result that already passed ``result.is_error``.

    Returns:
        The context, updated for whichever arm (if any) matched.
    """
    if tc.name == "load_tool":
        t_name = tc.arguments.get("tool_name")
        if isinstance(t_name, str) and t_name not in ctx.loaded_tools:
            ctx = ctx.with_tool_loaded(t_name)
            logger.info(
                TOOL_L2_LOADED,
                execution_id=ctx.execution_id,
                tool_name=t_name,
                turn=turn_number,
            )
    elif (
        watch_background_jobs
        and tc.name == _SHELL_COMMAND_TOOL_NAME
        and _coerced_bool(tc.arguments.get("background"), default=False)
    ):
        job_id = _parsed_background_job_id(result.content)
        if job_id is not None:
            ctx = ctx.with_background_job_watched(job_id, watching_since=clock.now())
            logger.info(
                EXECUTION_BACKGROUND_JOB_WATCH_STARTED,
                execution_id=ctx.execution_id,
                job_id=job_id,
                turn=turn_number,
            )
    elif tc.name == "load_tool_resource":
        t_name = tc.arguments.get("tool_name")
        r_id = tc.arguments.get("resource_id")
        if (
            isinstance(t_name, str)
            and isinstance(r_id, str)
            and (t_name, r_id) not in ctx.loaded_resources
        ):
            ctx = ctx.with_resource_loaded(t_name, r_id)
            logger.info(
                TOOL_L3_FETCHED,
                execution_id=ctx.execution_id,
                tool_name=t_name,
                resource_id=r_id,
                turn=turn_number,
            )
    elif tc.name == "compact_context":
        strategy = tc.arguments.get("strategy")
        reason = tc.arguments.get("reason")
        if isinstance(strategy, str) and isinstance(reason, str):
            preserve_markers = _coerced_bool(
                tc.arguments.get("preserve_markers"), default=True
            )
            ctx = ctx.model_copy(
                update={
                    "compaction_request": CompactionRequest(
                        strategy=strategy,
                        reason=reason,
                        preserve_markers=preserve_markers,
                    )
                }
            )
            logger.info(
                CONTEXT_BUDGET_AGENT_COMPACTION_REQUESTED,
                execution_id=ctx.execution_id,
                strategy=strategy,
                reason=reason,
                preserve_markers=preserve_markers,
                turn=turn_number,
            )
    return ctx


async def _invoke_tool_calls(
    tool_invoker: ToolInvokerProtocol,
    response: CompletionResponse,
    ctx: AgentContext,
    turn_number: int,
    turns: list[TurnRecord],
    *,
    tool_names: list[str],
) -> tuple[ToolResult, ...] | ExecutionResult:
    """Invoke every tool call in *response*, turning a raise into an ERROR result.

    Returns:
        The tool results, or an ERROR :class:`ExecutionResult` when
        invocation itself raised.
    """
    try:
        return await tool_invoker.invoke_all(
            response.tool_calls,
            execution_id=ctx.execution_id,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- returns ERROR result
        reraise_critical(exc)
        error_msg = (
            f"Tool execution failed on turn {turn_number}: "
            f"{type(exc).__name__}: {safe_error_description(exc)}"
        )
        log_exception_redacted(
            logger,
            EXECUTION_LOOP_ERROR,
            exc,
            execution_id=ctx.execution_id,
            turn=turn_number,
            tools=tool_names,
        )
        return _build_error_result(ctx, turns, error_msg)


def _append_tool_results(
    ctx: AgentContext,
    results: Sequence[ToolResult],
    *,
    tool_output_max_chars: int,
) -> AgentContext:
    """Abbreviate, fence and append every tool result to the conversation.

    Abbreviation runs first, on the raw result, so the elision marker sits
    inside the fence with the rest of the tool's bytes and the fence itself
    is never what gets cut. Injection detection still reads the WHOLE raw
    result: what was elided never reaches the model, but the attempt is
    what the telemetry records.

    Returns:
        The context with one ``TOOL`` message appended per result.
    """
    for result in results:
        content, elided = abbreviate_tool_output(
            result.content, max_chars=tool_output_max_chars
        )
        bounded = result
        if elided:
            logger.info(
                EXECUTION_TOOL_OUTPUT_ABBREVIATED,
                execution_id=ctx.execution_id,
                tool_call_id=result.tool_call_id,
                original_chars=len(result.content),
                elided_chars=elided,
                max_chars=tool_output_max_chars,
            )
            bounded = result.model_copy(update={"content": content})
        # Fence the tool output before it enters context so the next
        # LLM turn cannot mistake tool content for instructions.
        wrapped = wrap_tool_result(bounded, scanned=result.content)
        tool_msg = ChatMessage(role=MessageRole.TOOL, tool_result=wrapped)
        ctx = ctx.with_message(tool_msg)
    return ctx


async def execute_tool_calls(
    ctx: AgentContext,
    tool_invoker: ToolInvokerProtocol | None,
    response: CompletionResponse,
    turn_number: int,
    turns: list[TurnRecord],
    *,
    approval_gate: ApprovalGate | None = None,
    controls: ToolTurnControls | None = None,
) -> AgentContext | ExecutionResult:
    """Execute tool calls and append results to context.

    Args:
        ctx: The context the results are appended to.
        tool_invoker: What runs the calls; ``None`` ends the run in error.
        response: The completion carrying the calls.
        turn_number: The turn the calls belong to.
        turns: The run's turn records, appended to in place.
        approval_gate: Parks the run when a call escalates.
        controls: What the loop decided for this turn; ``None`` runs the
            turn under :meth:`ToolTurnControls.defaults`.

    Returns:
        The updated :class:`AgentContext` when execution should
        continue, or an :class:`ExecutionResult` (PARKED on
        approval-gate escalation, ERROR on missing invoker / tool
        failure).
    """
    turn_controls = controls if controls is not None else ToolTurnControls.defaults()
    if tool_invoker is None:
        error_msg = (
            f"LLM requested {len(response.tool_calls)} tool "
            f"call(s) but no tool invoker is available"
        )
        logger.error(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error=error_msg,
        )
        clear_last_turn_tool_calls(turns)
        return _build_error_result(ctx, turns, error_msg)

    tool_names = [tc.name for tc in response.tool_calls]
    logger.info(
        EXECUTION_LOOP_TOOL_CALLS,
        execution_id=ctx.execution_id,
        turn=turn_number,
        tools=tool_names,
    )

    results_or_error = await _invoke_tool_calls(
        tool_invoker, response, ctx, turn_number, turns, tool_names=tool_names
    )
    if isinstance(results_or_error, ExecutionResult):
        return results_or_error
    results = results_or_error

    record_resolved_tool_calls(turns, results)
    ctx = _append_tool_results(
        ctx, results, tool_output_max_chars=turn_controls.tool_output_max_chars
    )

    for tc, result in zip(response.tool_calls, results, strict=True):
        if result.is_error:
            continue
        ctx = _apply_tool_call_side_effect(
            ctx,
            tc,
            result,
            turn_number,
            clock=turn_controls.clock,
            watch_background_jobs=turn_controls.watch_background_jobs,
        )

    if approval_gate is not None:
        escalation = approval_gate.should_park(
            tool_invoker.pending_escalations,
        )
        if escalation is not None:
            return await _park_for_approval(
                ctx,
                escalation,
                approval_gate,
                turns,
            )

    return ctx
