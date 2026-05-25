"""Unit tests for the tracing subsystem.

Covers:

* Factory discriminator: ``DisabledTraceConfig`` -> ``NoopTraceHandler``;
  ``OtlpHttpTraceConfig`` -> ``OtlpTraceHandler``.
* ``NoopTraceHandler`` returns OTel's no-op tracer.
* ``llm_span`` / ``tool_span`` record the expected GenAI/tool
  attributes on the active span (via ``InMemorySpanExporter``).
* Tool span nested under LLM span is linked as a child in the
  exported span graph.
* Exceptions raised inside spans set ``Status(ERROR)`` and record the
  exception.
"""

import threading
from collections.abc import Generator
from typing import cast

import pytest
from opentelemetry import trace as _ot_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import NoOpTracer, StatusCode

from synthorg.observability.tracing import (
    DisabledTraceConfig,
    NoopTraceHandler,
    OtlpHttpTraceConfig,
    build_trace_handler,
    llm_span,
    tool_span,
)
from synthorg.observability.tracing.factory import build_trace_handler as _build

pytestmark = pytest.mark.unit


# OTel sets the global ``TracerProvider`` through a one-shot ``Once``
# guard; two tests running concurrently under pytest-xdist would
# otherwise race on the global slot. A module-level lock funnels the
# install/uninstall through one test at a time per worker process.
_tracer_provider_lock = threading.Lock()


def _clear_otel_tracer_provider() -> None:
    """Reset OTel's one-shot ``set_tracer_provider`` install guard.

    OpenTelemetry enforces that ``set_tracer_provider`` only succeeds
    once per process; subsequent calls are silently ignored. Tests
    that need to install a test provider (or rebuild one) have to
    clear the ``Once`` sentinel first. Centralising the reset keeps
    the SDK-private attribute names out of individual tests.
    """
    _ot_trace._TRACER_PROVIDER_SET_ONCE._done = False
    _ot_trace._TRACER_PROVIDER = None


def _restore_otel_tracer_provider(provider: TracerProvider | None) -> None:
    """Restore a previously-captured provider as the OTel global.

    Pairs with :func:`_clear_otel_tracer_provider`. The ``provider``
    argument is the raw ``_TRACER_PROVIDER`` attribute captured
    before the test, so it is either a concrete ``TracerProvider``
    (set by an earlier test) or ``None`` (nothing has set a
    provider yet this process). ``_done`` is set to ``True`` only
    when a concrete provider was present -- storing the ``_ProxyTracerProvider``
    here would create infinite recursion in ``get_tracer`` because
    the proxy's ``get_tracer`` delegates back to ``_TRACER_PROVIDER``.
    """
    _ot_trace._TRACER_PROVIDER = provider
    _ot_trace._TRACER_PROVIDER_SET_ONCE._done = provider is not None


def _snapshot_otel_tracer_provider() -> TracerProvider | None:
    """Capture the raw ``_TRACER_PROVIDER`` for later restoration.

    Uses the module-level attribute directly -- not
    ``get_tracer_provider()`` -- because the latter returns a
    ``_ProxyTracerProvider`` when nothing has been set, and stuffing
    that proxy back into ``_TRACER_PROVIDER`` makes subsequent
    ``get_tracer`` calls recurse forever (proxy delegates to
    ``_TRACER_PROVIDER`` which is the proxy itself).
    """
    return cast(TracerProvider | None, _ot_trace._TRACER_PROVIDER)


@pytest.fixture
def in_memory_tracer() -> Generator[InMemorySpanExporter]:
    """Install a global ``TracerProvider`` backed by InMemorySpanExporter.

    Cleans up after the test by resetting the global provider. Each
    test starts with a clean exporter.
    """
    with _tracer_provider_lock:
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        original = _snapshot_otel_tracer_provider()
        _clear_otel_tracer_provider()
        _ot_trace.set_tracer_provider(provider)
        try:
            yield exporter
        finally:
            provider.shutdown()
            _restore_otel_tracer_provider(original)


# -- Factory dispatch --------------------------------------------------------


def test_disabled_config_builds_noop_handler() -> None:
    handler = build_trace_handler(DisabledTraceConfig())
    assert isinstance(handler, NoopTraceHandler)
    assert isinstance(handler.get_tracer("x"), NoOpTracer)


def test_noop_handler_force_flush_and_shutdown_are_noop() -> None:
    import asyncio

    handler = NoopTraceHandler()
    asyncio.run(handler.force_flush())
    asyncio.run(handler.shutdown())


def test_factory_rejects_unknown_variant() -> None:
    """Defensive branch -- callers can't normally reach this."""
    with pytest.raises(ValueError, match="Unsupported TraceConfig variant"):
        _build("not-a-config")  # type: ignore[arg-type]


def test_otlp_http_config_builds_otlp_handler() -> None:
    from synthorg.observability.otlp_trace_handler import OtlpTraceHandler

    # Hold the lock through the whole test: OTel's ``Once`` guard for
    # ``set_tracer_provider`` is process-global and we're about to
    # install+uninstall a real provider. Running concurrently with
    # any other test that touches the global tracer would corrupt
    # each other's view of ``_TRACER_PROVIDER``.
    with _tracer_provider_lock:
        # Snapshot the pre-test provider so we can restore it after
        # shutdown. Clearing the globals without restoring leaves
        # ``_TRACER_PROVIDER=None`` + ``_done=False`` -- OTel's lazy
        # init then recurses through ``get_tracer_provider`` when
        # downstream tests call ``get_tracer``.
        original_provider = _snapshot_otel_tracer_provider()
        _clear_otel_tracer_provider()
        handler = build_trace_handler(
            OtlpHttpTraceConfig(
                endpoint="http://localhost:4318",
                sampling_ratio=0.0,  # Drop everything -- no network calls.
            )
        )
        try:
            assert isinstance(handler, OtlpTraceHandler)
        finally:
            import asyncio

            from synthorg.observability.otlp_trace_handler import (
                _reset_for_testing,
            )

            asyncio.run(handler.shutdown())
            # Restore OTel's global provider to the pre-test value so
            # other tests don't call ``get_tracer`` on the shut-down
            # provider or hit the lazy-init recursion path.
            _restore_otel_tracer_provider(original_provider)
            _reset_for_testing()


def test_otlp_http_config_rejects_header_with_newline() -> None:
    """Header values containing CR/LF are rejected (injection guard)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="CR/LF"):
        OtlpHttpTraceConfig(
            endpoint="http://collector:4318",
            headers=(("x-api-token", "secret\r\nInjected: bad"),),
        )


def test_otlp_http_config_rejects_header_name_with_newline() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="CR/LF"):
        OtlpHttpTraceConfig(
            endpoint="http://collector:4318",
            headers=(("x-evil\nInjected", "value"),),
        )


# -- llm_span ---------------------------------------------------------------


async def test_llm_span_sets_genai_attributes(
    in_memory_tracer: InMemorySpanExporter,
) -> None:
    async with llm_span(
        provider="TestProvider",
        model="example-large-001",
        input_tokens=42,
    ) as span:
        span.set_attribute("gen_ai.usage.output_tokens", 17)
        span.set_attribute("gen_ai.response.finish_reasons", ("stop",))
    spans = in_memory_tracer.get_finished_spans()
    assert len(spans) == 1
    recorded = spans[0]
    assert recorded.name == "chat example-large-001"
    attrs = dict(recorded.attributes or {})
    assert attrs["gen_ai.system"] == "TestProvider"
    assert attrs["gen_ai.request.model"] == "example-large-001"
    assert attrs["gen_ai.usage.input_tokens"] == 42
    assert attrs["gen_ai.usage.output_tokens"] == 17
    assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
    assert recorded.status.status_code == StatusCode.UNSET


async def test_llm_span_exception_sets_error_status(
    in_memory_tracer: InMemorySpanExporter,
) -> None:
    error_msg = "provider exploded"
    with pytest.raises(RuntimeError, match="provider exploded"):
        async with llm_span(provider="P", model="m"):
            raise RuntimeError(error_msg)
    spans = in_memory_tracer.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    # The redaction policy forbids ``span.record_exception`` (it
    # would serialise frame-locals); instead the helpers write
    # scrubbed OTel-semconv attributes from the safe description.
    assert not any(e.name == "exception" for e in span.events)
    attrs = dict(span.attributes or {})
    assert attrs["exception.type"] == "RuntimeError"
    assert attrs["exception.message"] == "RuntimeError: provider exploded"


# -- tool_span --------------------------------------------------------------


async def test_tool_span_sets_tool_attributes(
    in_memory_tracer: InMemorySpanExporter,
) -> None:
    async with tool_span(
        tool_name="web_search",
        tool_call_id="call-1",
    ) as span:
        span.set_attribute("tool.outcome", "success")
    spans = in_memory_tracer.get_finished_spans()
    assert len(spans) == 1
    recorded = spans[0]
    assert recorded.name == "tool web_search"
    attrs = dict(recorded.attributes or {})
    assert attrs["tool.name"] == "web_search"
    assert attrs["tool.call_id"] == "call-1"
    assert attrs["tool.outcome"] == "success"


async def test_tool_span_nested_under_llm_span_is_linked(
    in_memory_tracer: InMemorySpanExporter,
) -> None:
    """Tool invocation inside an LLM turn is linked as a child span."""
    async with llm_span(provider="P", model="m") as llm:
        async with tool_span(tool_name="t", tool_call_id="c") as tool:
            pass
        llm_context = llm.get_span_context()
        tool_context = tool.get_span_context()
        assert llm_context.trace_id == tool_context.trace_id
    spans = in_memory_tracer.get_finished_spans()
    # Order: child first (tool), then parent (llm).
    tool_recorded = next(s for s in spans if s.name.startswith("tool "))
    llm_recorded = next(s for s in spans if s.name.startswith("chat "))
    assert tool_recorded.parent is not None
    assert tool_recorded.parent.span_id == llm_recorded.context.span_id
    assert tool_recorded.context.trace_id == llm_recorded.context.trace_id


async def test_tool_span_exception_sets_error_status(
    in_memory_tracer: InMemorySpanExporter,
) -> None:
    error_msg = "tool failed"
    with pytest.raises(RuntimeError, match="tool failed"):
        async with tool_span(tool_name="t", tool_call_id="c"):
            raise RuntimeError(error_msg)
    spans = in_memory_tracer.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    assert not any(e.name == "exception" for e in span.events)
    attrs = dict(span.attributes or {})
    assert attrs["exception.type"] == "RuntimeError"
    assert attrs["exception.message"] == "RuntimeError: tool failed"
