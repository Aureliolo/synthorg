"""Structlog processor that injects OpenTelemetry trace context into logs.

When an OTel span is active on the current context, every log record
picks up ``trace_id`` / ``span_id`` / ``trace_flags`` fields so
downstream aggregators can join log lines to traces in a waterfall
view. When no span is active, the processor is a no-op; when the OTel
API is not installed, the import is lazy and also no-ops -- tracing
is an opt-in feature and logging must never depend on it.
"""

from typing import Any

from synthorg.core.critical_errors import reraise_critical


def inject_trace_context(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Attach the current span's trace/span ids to *event_dict*.

    Invariants:
        * If no valid span is active (including the OTel no-op span),
          the event dict is returned unchanged.
        * Existing keys are never overwritten -- callers that
          explicitly set ``trace_id`` win, and the OTel invalid
          sentinels are not written out.

    Args:
        event_dict: The structlog event dict being assembled.

    Returns:
        The (possibly augmented) event dict.
    """
    try:
        from opentelemetry import trace as _ot_trace  # noqa: PLC0415
    except ImportError:
        return event_dict

    # OTel raising from the current-span lookup would drop the log
    # record entirely (structlog processors bubble exceptions). The
    # correlation fields are best-effort enrichment; fail open.
    try:
        span = _ot_trace.get_current_span()
        context = span.get_span_context() if span is not None else None
    except Exception as exc:
        reraise_critical(exc)
        return event_dict
    if context is None or not context.is_valid:
        return event_dict
    # Format matches OpenTelemetry's canonical hex representation
    # (lowercase, zero-padded) so logs can be joined to spans by
    # direct string equality in Tempo, Jaeger, Datadog, etc.
    event_dict.setdefault("trace_id", f"{context.trace_id:032x}")
    event_dict.setdefault("span_id", f"{context.span_id:016x}")
    event_dict.setdefault("trace_flags", f"{context.trace_flags:02x}")
    return event_dict
