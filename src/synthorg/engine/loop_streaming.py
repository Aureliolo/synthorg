# module-kind: service
"""Streaming provider turn for the execution loops, with mid-turn interruption.

``call_provider`` (in :mod:`synthorg.engine.loop_helpers`) issues a per-turn
LLM call with ``provider.complete()``: a single awaited request that cannot be
stopped once in flight. ``stream_provider`` here is its streaming sibling. It
drains ``provider.stream()`` (keeping the provider's retry / rate-limit / cost
chokepoints intact), reassembles the deltas into the same
:class:`CompletionResponse` the loop already consumes, and, between chunks,
polls two interruption signals:

* an operator **cancellation** (``cancellation_checker``): the in-flight call is
  torn down and the run terminates ``CANCELLED``;
* a pending steering **REDIRECT** (``steering_inbox``): the in-flight call is
  torn down and the turn is re-issued so the operator's new constraint is in
  context, via the :class:`_TurnInterrupted` sentinel the loop handles.

The stream is always closed on exit (``_aclose_quietly`` in a ``finally``), so
an early return propagates ``aclose`` down the provider's wrapper chain and
cancels the underlying LiteLLM stream. Whatever partial token usage the stream
surfaced
before an abort is folded into the run cost, so an interrupt does not silently
under-count (streaming providers typically emit usage only on the terminal
chunk, so a mid-generation abort commonly carries none: that is the honest
floor, not a fabricated estimate).

``run_provider_turn`` is the single dispatcher the loops call: it streams when
streaming is enabled for the run, else falls back to ``call_provider``.

Resilience trade-off (accepted): ``provider.complete()`` retries the whole call
through the ``BaseCompletionProvider`` ``RetryHandler``, whereas a stream only
retries its initial connection setup: a retryable error surfacing mid-generation
(after content has streamed) terminates the turn ``ERROR`` rather than
transparently retrying, because the partial output has already been consumed and
re-issuing could duplicate side effects. This is why streaming is opt-in
(``engine.work_loop_streaming_enabled``) and capability-gated, with the
non-streaming ``call_provider`` path always available as the fallback.
"""

import copy
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final

from synthorg.core.completion_enums import FinishReason
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.intervention.loop_hook import resolve_steering_scope
from synthorg.engine.loop_helpers import build_result, call_provider
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TaskCancellationChecker,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_TURN_CANCELLED_MIDFLIGHT,
    EXECUTION_LOOP_TURN_INTERRUPTED,
    EXECUTION_LOOP_TURN_START,
    EXECUTION_LOOP_TURN_STREAMED,
)
from synthorg.providers.enums import StreamEventType
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    add_token_usage,
)
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

# Cancellation / steering are polled once every N consumed chunks rather than
# on every chunk, bounding the settings / brain reads while still catching an
# operator interrupt within a fraction of a second during active generation.
_INTERRUPT_POLL_EVERY_N_CHUNKS: Final[int] = 8


@dataclass(frozen=True)
class _TurnInterrupted:
    """A mid-turn steering REDIRECT aborted the in-flight streaming call.

    The loop folds ``partial_usage`` into the run cost and re-issues the turn;
    the top-of-turn steering check then adopts the directive into context.
    """

    partial_usage: TokenUsage


async def _is_cancelled(checker: TaskCancellationChecker | None) -> bool:
    """Poll the cancellation checker, degrading a read fault to ``False``.

    Returns:
        ``True`` when the task has been cancelled, else ``False`` (a transient
        checker fault must not abort a healthy in-flight call).
    """
    if checker is None:
        return False
    try:
        return await checker()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        return False


async def _has_pending_redirect(
    ctx: AgentContext,
    steering_inbox: SteeringInbox | None,
) -> bool:
    """Report whether a not-yet-adopted steering REDIRECT is pending.

    A REDIRECT is worth interrupting an in-flight call for; a HINT is not, so it
    is left for the turn boundary. Best-effort: a read fault degrades to
    ``False`` so steering never aborts a healthy call.

    Returns:
        ``True`` when at least one pending directive requires a replan.
    """
    if steering_inbox is None:
        return False
    scope = resolve_steering_scope(ctx)
    if scope is None:
        return False
    project_id, task_id, agent_id = scope
    try:
        directives = await steering_inbox.pending(
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            already_adopted=ctx.adopted_steering_ids,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        return False
    return any(directive.requires_replan for directive in directives)


async def _aclose_quietly(stream: AsyncIterator[StreamChunk]) -> None:
    """Close the stream on early exit, propagating only critical errors.

    An early break / cancel must tear down the underlying provider stream so
    the rate-limit-holding wrapper releases and the LiteLLM connection closes.
    A raw iterator without ``aclose`` is left untouched; a teardown fault is
    swallowed (``MemoryError`` / ``RecursionError`` propagate).
    """
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort teardown
        reraise_critical(exc)


def _accumulate_chunk(
    chunk: StreamChunk,
    content_parts: list[str],
    tool_calls: list[ToolCall],
) -> None:
    """Fold one stream chunk's payload into the reassembly accumulators."""
    if chunk.event_type is StreamEventType.CONTENT_DELTA and chunk.content:
        content_parts.append(chunk.content)
    elif (
        chunk.event_type is StreamEventType.TOOL_CALL_DELTA
        and chunk.tool_call_delta is not None
    ):
        tool_calls.append(chunk.tool_call_delta)


def _reassemble_response(
    *,
    content_parts: list[str],
    tool_calls: list[ToolCall],
    usage: TokenUsage,
    finish_reason: FinishReason | None,
    model_id: str,
) -> CompletionResponse:
    """Reassemble streamed deltas into a ``CompletionResponse``.

    Recovers the finish reason from the terminal chunk when the driver
    surfaced one, else infers it (tool calls imply ``TOOL_USE``, otherwise
    ``STOP``). An empty completion (no content, no tool calls) is normalised to
    ``ERROR`` so the built response is well-formed and the loop applies its own
    error handling, mirroring the non-streaming driver's empty-completion path.

    Returns:
        The reassembled :class:`CompletionResponse`.
    """
    content = "".join(content_parts) or None
    finish = finish_reason
    if finish is None:
        finish = FinishReason.TOOL_USE if tool_calls else FinishReason.STOP
    if (
        content is None
        and not tool_calls
        and finish not in (FinishReason.CONTENT_FILTER, FinishReason.ERROR)
    ):
        finish = FinishReason.ERROR
    return CompletionResponse(
        content=content,
        tool_calls=tuple(tool_calls),
        finish_reason=finish,
        usage=usage,
        model=model_id,
    )


@dataclass
class _StreamAccumulator:
    """Mutable in-flight reassembly state for one streamed turn.

    Held by the caller (not just the drain loop) so a mid-stream exception
    still leaves whatever usage / content the stream surfaced before the
    failure visible for cost folding.
    """

    content_parts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = ZERO_TOKEN_USAGE
    finish_reason: FinishReason | None = None


async def _check_interrupt(  # noqa: PLR0913
    ctx: AgentContext,
    turn_number: int,
    usage: TokenUsage,
    turns: list[TurnRecord],
    *,
    cancellation_checker: TaskCancellationChecker | None,
    steering_inbox: SteeringInbox | None,
) -> ExecutionResult | _TurnInterrupted | None:
    """Poll cancellation and steering, reporting either as an early exit.

    Returns:
        A ``CANCELLED`` :class:`ExecutionResult` when the operator
        cancelled; a :class:`_TurnInterrupted` when a steering REDIRECT is
        pending; or ``None`` when neither fired and draining should continue.
    """
    if await _is_cancelled(cancellation_checker):
        logger.info(
            EXECUTION_LOOP_TURN_CANCELLED_MIDFLIGHT,
            execution_id=ctx.execution_id,
            turn=turn_number,
        )
        return build_result(
            _fold_usage(ctx, usage),
            TerminationReason.CANCELLED,
            turns,
        )
    if await _has_pending_redirect(ctx, steering_inbox):
        logger.info(
            EXECUTION_LOOP_TURN_INTERRUPTED,
            execution_id=ctx.execution_id,
            turn=turn_number,
            reason="steering_redirect",
        )
        return _TurnInterrupted(usage)
    return None


async def _drain_stream(  # noqa: PLR0913
    stream: AsyncIterator[StreamChunk],
    acc: _StreamAccumulator,
    ctx: AgentContext,
    turn_number: int,
    turns: list[TurnRecord],
    *,
    cancellation_checker: TaskCancellationChecker | None,
    steering_inbox: SteeringInbox | None,
) -> ExecutionResult | _TurnInterrupted | None:
    """Drain *stream* into *acc*, polling for interruption between chunks.

    Mutates *acc* in place as chunks arrive, so a caller still sees the
    partial reassembly if this raises.

    Returns:
        ``None`` on normal stream completion (the reassembly is in *acc*);
        an ``ERROR`` :class:`ExecutionResult` on a provider ``ERROR`` event;
        or whatever :func:`_check_interrupt` reports on an operator
        cancellation or a pending steering REDIRECT.
    """
    index = 0
    async for chunk in stream:
        _accumulate_chunk(chunk, acc.content_parts, acc.tool_calls)
        if chunk.event_type is StreamEventType.USAGE and chunk.usage:
            acc.usage = chunk.usage
        elif (
            chunk.event_type is StreamEventType.DONE and chunk.finish_reason is not None
        ):
            acc.finish_reason = chunk.finish_reason
        elif chunk.event_type is StreamEventType.ERROR:
            stream_error = (
                f"Provider stream error on turn {turn_number}: {chunk.error_message}"
            )
            logger.warning(
                EXECUTION_LOOP_ERROR,
                execution_id=ctx.execution_id,
                turn=turn_number,
                error=stream_error,
            )
            # Fold whatever usage the stream billed before the error so
            # cost is not under-counted (parity with the cancel path).
            return build_result(
                _fold_usage(ctx, acc.usage),
                TerminationReason.ERROR,
                turns,
                error_message=stream_error,
            )

        if index % _INTERRUPT_POLL_EVERY_N_CHUNKS == 0:
            interrupt = await _check_interrupt(
                ctx,
                turn_number,
                acc.usage,
                turns,
                cancellation_checker=cancellation_checker,
                steering_inbox=steering_inbox,
            )
            if interrupt is not None:
                return interrupt
        index += 1
    return None


async def stream_provider(  # noqa: PLR0913
    ctx: AgentContext,
    provider: CompletionProvider,
    model_id: str,
    *,
    tool_defs: list[ToolDefinition] | None,
    config: CompletionConfig,
    turn_number: int,
    turns: list[TurnRecord],
    cancellation_checker: TaskCancellationChecker | None,
    steering_inbox: SteeringInbox | None,
) -> CompletionResponse | ExecutionResult | _TurnInterrupted:
    """Stream a per-turn LLM call, interruptible mid-flight.

    Drains ``provider.stream()`` and reassembles the deltas, polling the
    cancellation checker and steering inbox between chunks.

    Returns:
        The reassembled :class:`CompletionResponse` on success; a ``CANCELLED``
        :class:`ExecutionResult` when the operator cancelled in flight or an
        ``ERROR`` one on a provider error (matching ``call_provider``); or a
        :class:`_TurnInterrupted` when a steering REDIRECT should re-issue the
        turn.

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
        streaming=True,
    )

    acc = _StreamAccumulator()
    try:
        stream = await provider.stream(
            messages=copy.deepcopy(list(ctx.conversation)),
            model=model_id,
            tools=copy.deepcopy(tool_defs),
            config=copy.deepcopy(config),
        )
        try:
            early_exit = await _drain_stream(
                stream,
                acc,
                ctx,
                turn_number,
                turns,
                cancellation_checker=cancellation_checker,
                steering_inbox=steering_inbox,
            )
        finally:
            await _aclose_quietly(stream)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- returns ERROR result
        reraise_critical(exc)
        error_msg = (
            f"Provider error on turn {turn_number}: "
            f"{type(exc).__name__}: {safe_error_description(exc)}"
        )
        logger.warning(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Fold any usage the stream surfaced before the exception so a
        # mid-stream failure does not silently under-count provider spend.
        return build_result(
            _fold_usage(ctx, acc.usage),
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
        )

    if early_exit is not None:
        return early_exit

    logger.debug(
        EXECUTION_LOOP_TURN_STREAMED,
        execution_id=ctx.execution_id,
        turn=turn_number,
        content_chars=sum(len(part) for part in acc.content_parts),
        tool_calls=len(acc.tool_calls),
    )
    return _reassemble_response(
        content_parts=acc.content_parts,
        tool_calls=acc.tool_calls,
        usage=acc.usage,
        finish_reason=acc.finish_reason,
        model_id=model_id,
    )


def _fold_usage(ctx: AgentContext, usage: TokenUsage) -> AgentContext:
    """Return *ctx* with *usage* added to its accumulated cost.

    Returns:
        The context unchanged when *usage* is the additive identity, else a
        copy whose ``accumulated_cost`` includes the partial usage.
    """
    if usage is ZERO_TOKEN_USAGE:
        return ctx
    return ctx.model_copy(
        update={"accumulated_cost": add_token_usage(ctx.accumulated_cost, usage)}
    )


def fold_interrupt_usage(
    ctx: AgentContext,
    interrupted: _TurnInterrupted,
) -> AgentContext:
    """Fold an interrupted turn's partial usage into the run cost.

    Called by a loop when it re-issues a turn after a steering REDIRECT, so the
    tokens the aborted stream did surface are not lost from the run cost.

    Returns:
        The context with the interrupt's partial usage added to
        ``accumulated_cost``.
    """
    return _fold_usage(ctx, interrupted.partial_usage)


async def run_provider_turn(  # noqa: PLR0913
    ctx: AgentContext,
    provider: CompletionProvider,
    model_id: str,
    *,
    tool_defs: list[ToolDefinition] | None,
    config: CompletionConfig,
    turn_number: int,
    turns: list[TurnRecord],
    streaming_enabled: bool,
    cancellation_checker: TaskCancellationChecker | None,
    steering_inbox: SteeringInbox | None,
) -> CompletionResponse | ExecutionResult | _TurnInterrupted:
    """Issue the per-turn LLM call, streaming when enabled for the run.

    Streaming adds mid-turn cancellation and steer-interrupt; the non-streaming
    fallback (``call_provider``) is used when streaming is disabled for the run
    or unsupported by the model.

    Returns:
        A :class:`CompletionResponse` on success, an :class:`ExecutionResult`
        on a terminal outcome (error / cancellation), or a
        :class:`_TurnInterrupted` when the turn should be re-issued.
    """
    if streaming_enabled:
        return await stream_provider(
            ctx,
            provider,
            model_id,
            tool_defs=tool_defs,
            config=config,
            turn_number=turn_number,
            turns=turns,
            cancellation_checker=cancellation_checker,
            steering_inbox=steering_inbox,
        )
    return await call_provider(
        ctx,
        provider,
        model_id,
        tool_defs=tool_defs,
        config=config,
        turn_number=turn_number,
        turns=turns,
    )
