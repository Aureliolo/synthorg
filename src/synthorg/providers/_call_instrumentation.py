# module-kind: code
"""Span and metric instrumentation shared by ``complete`` and ``stream``.

Both provider entry points wrap their (retried) driver call in a
``provider.<call_type>`` span, record a ``provider_call_duration``
sample, and -- on failure -- scrub the exception before it can reach the
OTLP exporter.  Extracting the common shape keeps
``BaseCompletionProvider`` under its module-size budget and guarantees
the success and error paths stay in lockstep across both methods.
"""

import asyncio
from typing import Final, Protocol, runtime_checkable

from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import AttributeValue

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import (
    PROVIDER_CALL_ERROR,
    PROVIDER_HEALTH_RECORD_FAILED,
)
from synthorg.observability.metrics_hub import (
    record_provider_call_duration,
    record_provider_error,
)

from .cost_recording import (
    current_cost_context,
    emit_cost_record_from_context,
    emit_cost_record_from_usage,
)
from .errors import classify_provider_error
from .health import CallOutcome, ProviderOutcomeClass
from .health_recording import CallOutcomeRecorder
from .models import CompletionResponse, TokenUsage
from .resilience.retry import RetryResult

logger = get_logger(__name__)

_MILLISECONDS_PER_SECOND: Final[float] = 1000.0


@runtime_checkable
class _SpanSink(Protocol):
    """The minimal OTel span surface the instrumentation helpers write to.

    Annotating against this structural subset (rather than the concrete
    :class:`opentelemetry.trace.Span`) keeps the helpers honest about the
    two methods they actually call and lets recording test doubles
    satisfy the boundary without subclassing the full Span ABC.
    """

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        """Stamp a single attribute onto the span."""
        ...

    def set_status(self, status: Status | StatusCode) -> None:
        """Set the span's status (e.g. ``Status(StatusCode.ERROR)``)."""
        ...


def build_call_span_attributes(
    *,
    provider_label: str,
    model: str,
    message_count: int,
    tool_count: int,
) -> dict[str, str | int]:
    """Build the per-call span attribute bag.

    Args:
        provider_label: The resolved provider name.
        model: Model identifier for the call.
        message_count: Number of messages in the request.
        tool_count: Number of tools offered to the model.

    Returns:
        The attribute mapping for ``start_as_current_span``.
    """
    return {
        "provider.name": provider_label,
        "provider.model": model,
        "provider.message_count": message_count,
        "provider.tool_count": tool_count,
    }


def record_call_success(
    span: _SpanSink,
    *,
    provider_label: str,
    model: str,
    call_type: str,
    latency_ms: float,
) -> None:
    """Stamp latency on the span and record a duration sample.

    Args:
        span: The active ``provider.<call_type>`` span.
        provider_label: The resolved provider name.
        model: Model identifier for the call.
        call_type: ``"complete"`` or ``"stream"``.
        latency_ms: Wall-clock latency of the (retried) driver call.
    """
    span.set_attribute("provider.latency_ms", latency_ms)
    record_provider_call_duration(
        provider=provider_label,
        model=model,
        call_type=call_type,
        duration_sec=latency_ms / _MILLISECONDS_PER_SECOND,
    )


def merge_call_metadata(
    result: CompletionResponse,
    *,
    latency_ms: float,
    retry_info: RetryResult[CompletionResponse] | None,
) -> CompletionResponse:
    """Merge latency + retry telemetry into ``provider_metadata``.

    Args:
        result: The driver's completion response.
        latency_ms: Wall-clock latency of the (retried) call.
        retry_info: Retry bookkeeping when a retry handler ran, else None.

    Returns:
        A copy of ``result`` with the synthorg telemetry keys merged in.
    """
    metadata: dict[str, object] = {"_synthorg_latency_ms": latency_ms}
    if retry_info is not None:
        metadata["_synthorg_retry_count"] = max(0, retry_info.attempt_count - 1)
        if retry_info.retry_reason is not None:
            metadata["_synthorg_retry_reason"] = retry_info.retry_reason
    merged = dict(result.provider_metadata or {})
    merged.update(metadata)
    return result.model_copy(update={"provider_metadata": merged})


async def record_cost_if_in_scope(
    result: CompletionResponse,
    *,
    model: str,
    provider: str,
) -> None:
    """Emit a CostRecord when a ``cost_recording_scope`` is open.

    Sites without an open scope (probes, tests, and the engine path
    which records via ``record_execution_costs`` post-execution) see no
    change.  Recording errors are logged and swallowed inside
    ``emit_cost_record_from_context`` -- never surfaced to the caller.

    Args:
        result: The completion response carrying token usage.
        model: Model identifier for the call.
        provider: The resolved provider name.
    """
    ctx = current_cost_context()
    if ctx is not None:
        await emit_cost_record_from_context(ctx, result, model=model, provider=provider)


async def record_stream_cost_if_in_scope(
    usage: TokenUsage,
    *,
    model: str,
    provider: str,
) -> None:
    """Emit a CostRecord from a stream's terminal USAGE chunk in scope.

    The streaming counterpart to :func:`record_cost_if_in_scope`: when a
    ``cost_recording_scope`` is open, the token counts surfaced on a
    drained stream's terminal ``StreamEventType.USAGE`` chunk are
    recorded. Sites without an open scope see no change; recording
    errors are logged and swallowed inside ``emit_cost_record_from_usage``.

    Args:
        usage: Token usage from the stream's terminal USAGE chunk.
        model: Model identifier for the call.
        provider: The resolved provider name.
    """
    ctx = current_cost_context()
    if ctx is not None:
        await emit_cost_record_from_usage(ctx, usage, model=model, provider=provider)


async def record_image_cost_if_in_scope(
    usage: TokenUsage,
    *,
    model: str,
    provider: str,
) -> None:
    """Emit a CostRecord from an image-generation call's usage in scope.

    Image generation bills per image, so the usage carries zero token
    counts and the whole charge in ``cost``; a priced model
    (``cost_per_image > 0``) clears the zero-usage skip and is recorded,
    while an unpriced model produces a zero-usage record that is skipped
    like a free-tier token call. The invoker opens the enclosing
    ``cost_recording_scope`` for the ``image_generator`` tool; sites
    without an open scope see no change. Recording errors are swallowed
    inside ``emit_cost_record_from_usage``.

    Args:
        usage: Cost usage from the image-generation response.
        model: Model identifier for the call.
        provider: The resolved provider name.
    """
    ctx = current_cost_context()
    if ctx is not None:
        await emit_cost_record_from_usage(
            ctx,
            usage,
            model=model,
            provider=provider,
            call_category=LLMCallCategory.IMAGE_GENERATION,
        )


async def report_health_outcome(
    recorder: CallOutcomeRecorder | None,
    *,
    provider_label: str,
    model: str,
    success: bool,
    latency_ms: float,
    error_message: str | None = None,
    outcome_class: ProviderOutcomeClass | None = None,
) -> None:
    """Report one finished call to the driver's bound health recorder.

    Best-effort: the call already has its answer for the caller, so a tracker
    failure must not turn a completed call into an error, nor a failed call
    into a different error than the one that actually happened.

    Run attribution is read here rather than passed in, from the same
    ``cost_recording_scope`` the cost chokepoint reads a few lines later, so
    a caller cannot report a health outcome under one agent while its spend
    lands under another. Outside a scope both are absent.

    Args:
        recorder: Sink bound to this driver, or ``None`` when unmonitored.
        provider_label: Provider named in the warning if recording fails.
        model: Model this call named.
        success: Whether the call succeeded.
        latency_ms: Round-trip time measured for the call.
        error_message: Redacted failure description, when it failed.
        outcome_class: Classified outcome, when the caller classified it.

    Raises:
        asyncio.CancelledError: Propagated so shutdown is not swallowed.
    """
    if recorder is None:
        return
    ctx = current_cost_context()
    try:
        await recorder(
            CallOutcome(
                success=success,
                response_time_ms=latency_ms,
                error_message=error_message,
                model=model,
                outcome_class=outcome_class,
                agent_id=ctx.agent_id if ctx is not None else None,
                task_id=ctx.task_id if ctx is not None else None,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see below
        # lint-allow: swallow-ok -- health is a side channel; the call itself
        # already has its outcome and must report that, not this.
        reraise_critical(exc)
        logger.warning(
            PROVIDER_HEALTH_RECORD_FAILED,
            provider=provider_label,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def record_call_failure(
    span: _SpanSink,
    exc: Exception,
    *,
    model: str,
    provider_label: str,
    call_type: str,
    latency_ms: float,
) -> None:
    """Scrub + log a failed provider call, stamp the span, record metrics.

    ``logger.exception`` (what TRY400 suggests) would attach a traceback
    whose serialized frame-locals can leak provider credentials (API
    keys in headers, connection URLs with user:pass), so this uses
    ``log_exception_redacted`` + scrubbed span attributes instead.  The
    span is opened with ``set_status_on_exception=False`` so the ERROR
    status must be set manually here; ``Status.description`` is left
    unset so the OTLP exporter never carries the raw provider string
    (the scrubbed text is exposed via ``exception.message``).

    Args:
        span: The active ``provider.<call_type>`` span.
        exc: The exception raised by the driver call.
        model: Model identifier for the call.
        provider_label: The resolved provider name.
        call_type: ``"complete"`` or ``"stream"``.
        latency_ms: Wall-clock latency until the failure.
    """
    log_exception_redacted(
        logger, PROVIDER_CALL_ERROR, exc, model=model, latency_ms=latency_ms
    )
    span.set_attribute("exception.type", type(exc).__name__)
    span.set_attribute("exception.message", safe_error_description(exc))
    span.set_attribute("provider.latency_ms", latency_ms)
    span.set_status(Status(StatusCode.ERROR))
    record_provider_error(
        provider=provider_label,
        model=model,
        error_class=classify_provider_error(exc),
    )
    record_provider_call_duration(
        provider=provider_label,
        model=model,
        call_type=call_type,
        duration_sec=latency_ms / _MILLISECONDS_PER_SECOND,
    )
