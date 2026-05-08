"""Tests for OTLP request-level span instrumentation in the API middleware.

Each HTTP request is wrapped in a ``http.request`` span so distributed
traces line up with the structured-log stream. The span carries
OTel-semconv attributes (``http.request.method``, ``http.route``,
``http.response.status_code``) plus the in-process correlation id, and
records exceptions on the unhappy path.

Tracer-provider singleton handling: OpenTelemetry refuses to override
the active provider once installed. The fixture installs the provider
once per session via the SDK's hidden override path
(``trace._TRACER_PROVIDER = None`` then ``set_tracer_provider``), then
each test clears the in-memory exporter to start fresh.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def _tracer_setup() -> Iterator[InMemorySpanExporter]:
    """Install a tracer provider once per module and yield its exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # OTel forbids overriding via the public API; reach behind it for the
    # test environment so a real provider replaces the no-op one cached
    # at module import. This is a documented escape hatch in the SDK
    # source.
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)
    # Re-import the middleware module so its module-level
    # ``_tracer = trace.get_tracer(__name__)`` re-binds against the
    # newly-installed provider.
    import importlib

    import synthorg.api.middleware as mw

    importlib.reload(mw)
    return exporter


@pytest.fixture
def in_memory_exporter(
    _tracer_setup: InMemorySpanExporter,
) -> InMemorySpanExporter:
    """Return the shared exporter, cleared at function scope."""
    _tracer_setup.clear()
    return _tracer_setup


async def _drive_success(middleware: Any, status_code: int = 200) -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "headers": [],
        "query_string": b"",
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        # Status capture happens via the middleware's wrapped send.
        pass

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": status_code})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware.app = app
    await middleware(scope, receive, send)  # type: ignore[arg-type]


async def _drive_failure(middleware: Any, exc: Exception) -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "headers": [],
        "query_string": b"",
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        pass

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise exc

    middleware.app = app
    await middleware(scope, receive, send)  # type: ignore[arg-type]


async def test_middleware_emits_span_on_success(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    import synthorg.api.middleware as mw

    middleware = mw.RequestLoggingMiddleware(app=lambda *_: None)  # type: ignore[arg-type]
    await _drive_success(middleware, status_code=200)

    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "http.request"
    attrs = dict(span.attributes or {})
    assert attrs.get("http.request.method") == "GET"
    assert attrs.get("http.route") == "/api/v1/health"
    assert attrs.get("http.response.status_code") == 200
    assert "synthorg.correlation_id" in attrs


async def test_middleware_records_exception_on_failure(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    import synthorg.api.middleware as mw

    middleware = mw.RequestLoggingMiddleware(app=lambda *_: None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="boom"):
        await _drive_failure(middleware, RuntimeError("boom"))

    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    # OTel's ``record_exception`` writes its own description in newer
    # SDK versions, overwriting whatever ``set_status`` carried; the
    # invariant that matters is ``ERROR`` + ``RuntimeError`` reachable
    # in the description.
    assert "RuntimeError" in (span.status.description or "")
    event_names = [evt.name for evt in (span.events or ())]
    assert "exception" in event_names
