"""Span context-manager helpers for LLM and tool instrumentation.

``llm_span`` wraps an LLM provider call; ``tool_span`` wraps a tool
invocation. Both use OTel's GenAI semantic conventions
(``gen_ai.*``) and tool semantic conventions (``tool.*``) so
Jaeger / Tempo / Honeycomb / Grafana display structured fields in
the UI.

The helpers read their tracer via :func:`get_tracer`, which in turn
forwards to the globally installed :class:`TracerProvider`. With
tracing disabled, ``get_tracer`` returns OTel's built-in
:class:`NoOpTracer` and these context managers cost effectively
nothing.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from opentelemetry import trace as _ot_trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import safe_error_description

_INSTRUMENTATION_NAME = "synthorg"


def get_tracer(name: str = _INSTRUMENTATION_NAME) -> Tracer:
    """Return the globally configured tracer for *name*.

    With no provider installed, this yields OTel's built-in
    :class:`~opentelemetry.trace.NoOpTracer`.
    """
    return _ot_trace.get_tracer(name)


def _record_span_exception(span: Span, exc: Exception) -> None:
    """Record a scrubbed exception on *span* and mark its status ERROR.

    Critical errors (``MemoryError`` / ``RecursionError``) re-raise first. The
    SDK's auto-exception handler is disabled at span start (to keep frame-locals
    out of the OTLP exporter), so the OTel-semconv exception attributes are
    written here from the redaction-safe description instead. See
    ``docs/reference/sec-prompt-safety.md``.
    """
    reraise_critical(exc)
    span.set_attribute("exception.type", type(exc).__name__)
    span.set_attribute("exception.message", safe_error_description(exc))
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


@asynccontextmanager
async def llm_span(
    *,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens_callback: Callable[[], int | None] | None = None,
    tracer: Tracer | None = None,
) -> AsyncIterator[Span]:
    """Wrap an LLM completion call in a ``chat {model}`` span.

    Attributes set on entry follow OTel's GenAI semantic conventions
    (``gen_ai.system``, ``gen_ai.request.model``, and when known
    ``gen_ai.usage.input_tokens``). Callers should set
    ``gen_ai.usage.output_tokens`` (and ideally ``gen_ai.response.model``
    / ``gen_ai.response.finish_reasons``) on the span via
    :meth:`Span.set_attribute` once the response is available, OR supply
    *output_tokens_callback* for the streaming drain path (below) where the
    count is only known after the context exits.

    For the streaming drain path the output-token count is only known
    after the iterator is exhausted, which can be past the ``yield``;
    *output_tokens_callback* has the span stamp ``gen_ai.usage.output_tokens``
    from the callback in the ``finally`` block, so a streamed call records its
    output tokens even when the caller cannot set the attribute before the
    context exits. The ``provider.stream`` span in ``providers/base.py``
    intentionally closes at time-to-first-iterator, so that span never sees the
    drained count; this callback is the seam that recovers it on the wrapping
    span.

    Exceptions raised inside the context manager are recorded on the
    span and the span status is set to ``ERROR`` before the
    exception re-raises.
    """
    tracer = tracer or get_tracer()
    # ``record_exception=False, set_status_on_exception=False`` keep the
    # OTel SDK from auto-serialising frame-locals into the OTLP exporter
    # via its built-in exception handler. The except branch sets the
    # OTel-semconv exception attributes from the scrubbed description
    # so this transport stays on the same redaction posture as
    # structlog. See ``docs/reference/sec-prompt-safety.md``.
    with tracer.start_as_current_span(
        f"chat {model}",
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        span.set_attribute("gen_ai.system", provider)
        span.set_attribute("gen_ai.request.model", model)
        if input_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        try:
            yield span
        except Exception as exc:
            _record_span_exception(span, exc)
            raise
        finally:
            if output_tokens_callback is not None:
                # Instrumentation must never alter the request outcome: a
                # callback that raises here would mask the original failure
                # (or turn a success into an error) by escaping the
                # ``finally``. Record the callback failure on the span and
                # swallow it instead, keeping the in-flight exception (if any)
                # intact. Critical errors still propagate via ``reraise_critical``.
                try:
                    resolved = output_tokens_callback()
                    if resolved is not None:
                        span.set_attribute("gen_ai.usage.output_tokens", resolved)
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    span.set_attribute("exception.type", type(exc).__name__)
                    span.set_attribute("exception.message", safe_error_description(exc))


@asynccontextmanager
async def tool_span(
    *,
    tool_name: str,
    tool_call_id: str,
    tracer: Tracer | None = None,
) -> AsyncIterator[Span]:
    """Wrap a tool invocation in a ``tool {tool_name}`` span.

    When this context manager is entered inside an active
    :func:`llm_span`, OTel's context propagation links the tool span
    as a child in the waterfall view -- no explicit parent wiring
    needed.

    Callers must set ``tool.outcome`` (``"success"`` / ``"error"`` /
    ``"timeout"``) on the span once the invocation completes so
    operators can filter by outcome in the tracing UI.
    """
    tracer = tracer or get_tracer()
    # See the ``llm_span`` block above for the OTLP redaction
    # rationale: the SDK's auto-exception handler is disabled and the
    # except branch writes scrubbed OTel-semconv attributes directly.
    with tracer.start_as_current_span(
        f"tool {tool_name}",
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("tool.call_id", tool_call_id)
        try:
            yield span
        except Exception as exc:
            _record_span_exception(span, exc)
            raise
