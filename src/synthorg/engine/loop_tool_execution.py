"""Tool-call execution helpers for execution loops.

Extracted from :mod:`synthorg.engine.loop_helpers` to keep the main
helpers module under the project size limit.
"""

import re
from typing import TYPE_CHECKING, Final

from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.engine.prompt_safety import (
    TAG_CODE_DIFF,
    TAG_CONFIG_VALUE,
    TAG_CRITERIA_JSON,
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    TAG_TASK_FACT,
    TAG_TOOL_ARGUMENTS,
    TAG_TOOL_RESULT,
    TAG_UNTRUSTED_ARTIFACT,
    wrap_untrusted,
)
from synthorg.observability import get_logger, scrub_secret_tokens
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PARK_TASKLESS,
)
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_TOOL_CALLS,
)
from synthorg.observability.events.tool import (
    TOOL_INJECTION_PATTERN_DETECTED,
    TOOL_L2_LOADED,
    TOOL_L3_FETCHED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolResult

if TYPE_CHECKING:
    from synthorg.approval.models import EscalationInfo
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.context import AgentContext
    from synthorg.providers.models import CompletionResponse
    from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)


# SEC-1 / audit finding 92: common prompt-injection patterns that a
# tool might return in an attempt to take over the next LLM turn.
# Matches are flagged via ``TOOL_INJECTION_PATTERN_DETECTED`` for
# telemetry; the tool result is still wrapped in the fence, not
# rejected (rejection would break legitimate tools that echo user
# text in responses).
# Closing-tag look-alikes for every untrusted-content fence declared
# in ``synthorg.engine.prompt_safety``.  Deriving the regex set from
# the shared ``TAG_*`` constants keeps the advisory detector in sync
# with the wrapper: if a new tag is added (or one is renamed), this
# list updates automatically instead of silently drifting.  Optional
# whitespace before ``>`` mirrors ``_escape_closing_tag`` so lenient
# variants (``</task-data >`` / ``</task-data\t>``) still trip.
_FENCE_TAGS: Final[tuple[str, ...]] = (
    TAG_TASK_DATA,
    TAG_TASK_FACT,
    TAG_TOOL_RESULT,
    TAG_TOOL_ARGUMENTS,
    TAG_UNTRUSTED_ARTIFACT,
    TAG_CODE_DIFF,
    TAG_CONFIG_VALUE,
    TAG_CRITERIA_JSON,
    TAG_PEER_CONTRIBUTION,
)

_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"ignore\s+(all|previous|prior)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(all|previous|prior)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"system\s*:\s*you", re.IGNORECASE),
    *tuple(
        re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE) for tag in _FENCE_TAGS
    ),
)


def _wrap_tool_result(result: ToolResult) -> ToolResult:
    """Return *result* with its ``content`` wrapped in ``<tool-result>``.

    Also emits ``TOOL_INJECTION_PATTERN_DETECTED`` when the raw
    content matches a known injection pattern (see
    :data:`_INJECTION_PATTERNS`). Detection is advisory; the wrap
    happens unconditionally so a malicious tool cannot escape the
    fence even if no pattern matches.
    """
    raw = result.content
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(raw)
        if match is not None:
            # SEC-1: scrub the telemetry sample before emitting -- if the
            # attacker embedded a credential inside the injection payload,
            # the raw ``sample=`` field would otherwise leak it into logs.
            logger.warning(
                TOOL_INJECTION_PATTERN_DETECTED,
                tool_call_id=result.tool_call_id,
                pattern=pattern.pattern,
                sample=scrub_secret_tokens(raw[: min(200, len(raw))]),
            )
            break
    return result.model_copy(
        update={"content": wrap_untrusted(TAG_TOOL_RESULT, raw)},
    )


def _build_error_result(
    ctx: AgentContext,
    turns: list[TurnRecord],
    error_message: str,
    *,
    metadata: dict[str, object] | None = None,
) -> ExecutionResult:
    """Inline build_result helper avoiding a circular import."""
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
            update={"tool_calls_made": (), "tool_call_fingerprints": ()},
        )


async def _park_for_approval(
    ctx: AgentContext,
    escalation: EscalationInfo,
    approval_gate: ApprovalGate,
    turns: list[TurnRecord],
) -> ExecutionResult:
    """Park the context for approval and return a PARKED or ERROR result."""
    from synthorg.engine.loop_helpers import build_result  # noqa: PLC0415

    agent_id = str(ctx.identity.id)
    task_id: str | None = None
    if ctx.task_execution is not None:
        task_id = ctx.task_execution.task.id
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
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
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
        },
    )


async def execute_tool_calls(  # noqa: PLR0913, C901
    ctx: AgentContext,
    tool_invoker: ToolInvokerProtocol | None,
    response: CompletionResponse,
    turn_number: int,
    turns: list[TurnRecord],
    *,
    approval_gate: ApprovalGate | None = None,
) -> AgentContext | ExecutionResult:
    """Execute tool calls and append results to context."""
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

    try:
        results = await tool_invoker.invoke_all(
            response.tool_calls,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        error_msg = (
            f"Tool execution failed on turn {turn_number}: {type(exc).__name__}: {exc}"
        )
        logger.exception(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error=error_msg,
            tools=tool_names,
        )
        return _build_error_result(ctx, turns, error_msg)

    for result in results:
        # SEC-1: fence the tool output before it enters context so the
        # next LLM turn cannot mistake tool content for instructions.
        wrapped = _wrap_tool_result(result)
        tool_msg = ChatMessage(
            role=MessageRole.TOOL,
            tool_result=wrapped,
        )
        ctx = ctx.with_message(tool_msg)

    for tc, result in zip(response.tool_calls, results, strict=True):
        if result.is_error:
            continue
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
