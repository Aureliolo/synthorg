"""Shared stateless helpers for all ExecutionLoop implementations.

Each function operates on explicit parameters (no ``self``), keeping
loop implementations (ReAct, Plan-and-Execute, etc.) thin and focused
on their control-flow logic.

Wrap-ownership note: this module is stateless control flow only.
The :func:`wrap_untrusted` responsibility lives upstream of the
loop:

- Tool-result wrapping is owned by
  :func:`synthorg.engine.loop_tool_execution._wrap_tool_result` (see
  also ``_FENCE_TAGS`` in that module).
- User-message wrapping is owned by the per-strategy prompt builders
  that produce the initial ``ChatMessage`` payload, e.g.
  :func:`synthorg.engine.prompt_validation.format_task_instruction`,
  :func:`synthorg.engine.decomposition.llm_prompt.build_task_message`,
  and ``AgentIntake._build_prompt`` in
  :mod:`synthorg.engine.intake.strategies.agent_intake`.

Editors of this module should NOT add ``wrap_untrusted`` calls here:
those would re-wrap already-fenced payloads and weaken the
untrusted-content fence contract. Every LLM message build site under
``src/synthorg/engine/`` is already covered by the upstream wrappers.
"""

import copy
import hashlib
import json

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.execution.turn import BehaviorTag, NodeType, TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_TURN_START,
)
from synthorg.observability.events.tracing import SPAN_ATTRIBUTE_WRITE_FAILED
from synthorg.observability.tracing import llm_span
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolCall,
    ToolDefinition,
    add_token_usage,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol

from .loop_protocol import (
    ExecutionResult,
    TerminationReason,
)

logger = get_logger(__name__)


async def call_provider(  # noqa: PLR0913
    ctx: AgentContext,
    provider: CompletionProvider,
    model_id: str,
    tool_defs: list[ToolDefinition] | None,
    config: CompletionConfig,
    turn_number: int,
    turns: list[TurnRecord],
) -> CompletionResponse | ExecutionResult:
    """Call ``provider.complete()``, returning an error result on failure.

    Args:
        ctx: Current agent context with conversation history.
        provider: LLM completion provider.
        model_id: Model identifier to use.
        tool_defs: Optional tool definitions to pass to the LLM.
        config: Completion config (temperature, max_tokens, etc.).
        turn_number: Current turn number (1-indexed).
        turns: Accumulated turn records.

    Returns:
        ``CompletionResponse`` on success, or ``ExecutionResult`` on error.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
    char_count = sum(len(m.content or "") for m in ctx.conversation)
    logger.info(
        EXECUTION_LOOP_TURN_START,
        execution_id=ctx.execution_id,
        turn=turn_number,
        message_count=len(ctx.conversation),
        char_count_estimate=char_count,
    )
    provider_name = type(provider).__name__
    try:
        async with llm_span(
            provider=provider_name,
            model=model_id,
        ) as span:
            # Deep-copy the provider payload at the system boundary so
            # a driver that normalizes messages/tools/config in place
            # cannot leak those mutations back into engine state.
            response = await provider.complete(
                messages=copy.deepcopy(list(ctx.conversation)),
                model=model_id,
                tools=copy.deepcopy(tool_defs),
                config=copy.deepcopy(config),
            )
            # Span attribute writes must never mask a successful
            # provider response: if OTel throws here the outer
            # ``llm_span`` context manager would re-raise and the
            # caller would treat the turn as a provider failure.
            try:
                usage = response.usage
                if usage is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
                    span.set_attribute(
                        "gen_ai.usage.output_tokens", usage.output_tokens
                    )
                if response.finish_reason is not None:
                    span.set_attribute(
                        "gen_ai.response.finish_reasons",
                        (response.finish_reason.value,),
                    )
                if response.model:
                    span.set_attribute("gen_ai.response.model", response.model)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    SPAN_ATTRIBUTE_WRITE_FAILED,
                    execution_id=ctx.execution_id,
                    turn=turn_number,
                    reason="span_attribute_write_failed",
                )
            return response
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        error_msg = f"Provider error on turn {turn_number}: {type(exc).__name__}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
        )


def check_response_errors(
    ctx: AgentContext,
    response: CompletionResponse,
    turn_number: int,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return an error result for CONTENT_FILTER or ERROR responses.

    When returning an error result, the result's context includes the
    failing turn's token usage so callers see accurate totals.
    """
    if response.finish_reason not in (
        FinishReason.CONTENT_FILTER,
        FinishReason.ERROR,
    ):
        return None
    error_msg = f"LLM returned {response.finish_reason.value} on turn {turn_number}"
    logger.error(
        EXECUTION_LOOP_ERROR,
        execution_id=ctx.execution_id,
        turn=turn_number,
        error=error_msg,
    )
    updated_ctx = ctx.model_copy(
        update={
            "turn_count": ctx.turn_count + 1,
            "accumulated_cost": add_token_usage(ctx.accumulated_cost, response.usage),
        },
    )
    return build_result(
        updated_ctx,
        TerminationReason.ERROR,
        turns,
        error_message=error_msg,
    )


def get_tool_definitions(
    tool_invoker: ToolInvokerProtocol | None,
    loaded_tools: frozenset[str] = frozenset(),
) -> list[ToolDefinition] | None:
    """Extract disclosure-aware tool definitions from the invoker.

    Returns full ``ToolDefinition`` objects only for tools in
    ``loaded_tools`` plus the three discovery tools.  When
    ``loaded_tools`` is empty, only discovery tools are returned.

    Args:
        tool_invoker: Tool invoker (can be ``None``).
        loaded_tools: Tool names with L2 active in context.

    Returns:
        List of tool definitions, or ``None`` if no invoker.
    """
    if tool_invoker is None:
        return None
    defs = tool_invoker.get_loaded_definitions(loaded_tools)
    return list(defs) if defs else None


def response_to_message(response: CompletionResponse) -> ChatMessage:
    """Convert a ``CompletionResponse`` to an assistant ``ChatMessage``.

    Returns:
        A :class:`ChatMessage` with role ASSISTANT carrying the
        response content and tool calls.
    """
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=response.content,
        tool_calls=response.tool_calls,
    )


def make_turn_record(  # noqa: PLR0913
    turn_number: int,
    response: CompletionResponse,
    *,
    call_category: LLMCallCategory | None = None,
    provider_metadata: dict[str, object] | None = None,
    extra_node_types: tuple[NodeType, ...] = (),
    behavior_tags: tuple[BehaviorTag, ...] = (),
    prior_tool_call_count: int = 0,
    tool_response_tokens: int = 0,
) -> TurnRecord:
    """Create a ``TurnRecord`` from a provider response.

    Automatically derives ``LLM_CALL`` (always) and ``TOOL_INVOCATION``
    (when tool calls are present). Callers pass additional node types
    via *extra_node_types* for checks that ran this turn (quality,
    budget, stagnation).

    Args:
        turn_number: 1-indexed turn number.
        response: Provider completion response.
        call_category: Optional LLM call category.
        provider_metadata: Optional metadata dict from
            ``CompletionResponse.provider_metadata``. Keys
            ``_synthorg_latency_ms``, ``_synthorg_cache_hit``,
            ``_synthorg_retry_count``, and ``_synthorg_retry_reason``
            are extracted when present.
        extra_node_types: Additional node types beyond the
            auto-derived LLM_CALL and TOOL_INVOCATION.
        behavior_tags: Tags inferred by BehaviorTaggerMiddleware.
        prior_tool_call_count: Cumulative tool calls before this
            turn (for PTE computation).
        tool_response_tokens: Tokens from tool responses this
            turn (for PTE computation).

    Returns:
        A :class:`TurnRecord` carrying the input/output token usage,
        derived node types, behaviour tags, and extracted metadata
        fields.
    """
    md = provider_metadata or {}
    latency_ms_raw = md.get("_synthorg_latency_ms")
    cache_hit_raw = md.get("_synthorg_cache_hit")
    retry_count_raw = md.get("_synthorg_retry_count")
    retry_reason_raw = md.get("_synthorg_retry_reason")

    latency_ms: float | None = None
    if isinstance(latency_ms_raw, (int, float)):
        latency_ms = float(latency_ms_raw)

    cache_hit: bool | None = None
    if isinstance(cache_hit_raw, bool):
        cache_hit = cache_hit_raw

    retry_count: int | None = None
    if isinstance(retry_count_raw, int):
        retry_count = retry_count_raw

    retry_reason: str | None = None
    if isinstance(retry_reason_raw, str):
        retry_reason = retry_reason_raw

    # Auto-derive base node types from response content.
    derived: list[NodeType] = [NodeType.LLM_CALL]
    if response.tool_calls:
        derived.append(NodeType.TOOL_INVOCATION)
    node_types = tuple(derived) + extra_node_types

    return TurnRecord(
        turn_number=turn_number,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost=response.usage.cost,
        tool_calls_made=tuple(tc.name for tc in response.tool_calls),
        tool_call_fingerprints=compute_fingerprints(response.tool_calls),
        finish_reason=response.finish_reason,
        call_category=call_category,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        retry_count=retry_count,
        retry_reason=retry_reason,
        node_types=node_types,
        behavior_tags=behavior_tags,
        prior_tool_call_count=prior_tool_call_count,
        tool_response_tokens=tool_response_tokens,
    )


def compute_fingerprints(
    tool_calls: tuple[ToolCall, ...],
) -> tuple[str, ...]:
    """Compute sorted deterministic fingerprints from tool calls.

    Each fingerprint is ``name:args_hash`` where ``args_hash`` is a
    16-char hex prefix of the SHA-256 hash of the canonicalized
    arguments JSON.

    Args:
        tool_calls: Tool calls to fingerprint.

    Returns:
        Sorted tuple of fingerprint strings.
    """
    fingerprints = []
    for tc in tool_calls:
        canonical = json.dumps(
            tc.arguments,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        args_hash = hashlib.sha256(
            canonical.encode(),
        ).hexdigest()[:16]
        fingerprints.append(f"{tc.name}:{args_hash}")
    return tuple(sorted(fingerprints))


def classify_turn(
    turn_number: int,
    response: CompletionResponse,
    ctx: AgentContext,
    *,
    is_planning_phase: bool = False,
    is_system_prompt: bool = False,
) -> LLMCallCategory:
    """Classify an LLM turn using the rules-based classifier.

    Args:
        turn_number: 1-indexed turn number.
        response: Provider completion response.
        ctx: Agent execution context.
        is_planning_phase: Whether this is a planning-phase turn.
        is_system_prompt: Whether this is a system prompt turn.

    Returns:
        The call category for this turn.
    """
    from synthorg.budget.call_classifier import (  # noqa: PLC0415
        ClassificationContext,
        classify_call,
    )

    task_id = "unknown"
    if ctx.task_execution is not None:
        task_id = str(ctx.task_execution.task.id)

    classification_ctx = ClassificationContext(
        turn_number=turn_number,
        agent_id=str(ctx.identity.id),
        task_id=task_id,
        is_planning_phase=is_planning_phase,
        is_system_prompt=is_system_prompt,
        tool_calls_made=tuple(tc.name for tc in response.tool_calls),
        agent_role=ctx.identity.role,
    )
    return classify_call(classification_ctx)


def build_result(
    ctx: AgentContext,
    reason: TerminationReason,
    turns: list[TurnRecord],
    *,
    error_message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ExecutionResult:
    """Build an ``ExecutionResult`` from loop state.

    Returns:
        An :class:`ExecutionResult` carrying the current context,
        termination reason, turn records, and optional error /
        metadata payload.
    """
    return ExecutionResult(
        context=ctx,
        termination_reason=reason,
        turns=tuple(turns),
        error_message=error_message,
        metadata=metadata or {},
    )
