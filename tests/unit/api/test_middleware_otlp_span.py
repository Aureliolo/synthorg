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

import importlib
from collections.abc import Generator
from typing import Any, cast

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
def _tracer_setup() -> Generator[InMemorySpanExporter]:
    """Install a tracer provider once per module and restore on teardown.

    The fixture pokes ``trace._TRACER_PROVIDER`` and
    ``trace._TRACER_PROVIDER_SET_ONCE._done`` -- private SDK globals --
    because OpenTelemetry refuses to override the active provider via
    its public API. Module scope is deliberate: a function-scoped
    install would race with the middleware module's
    ``_tracer = trace.get_tracer(__name__)`` cache. xdist runs tests
    via ``--dist=loadfile`` which keeps each test module on a single
    worker, so the private-globals mutation cannot collide with other
    OTel-instrumented test modules. If a future test file installs its
    own provider, both fixtures must coordinate (or share this one).

    Teardown captures the pre-mutation provider plus the ``_done``
    sentinel and restores them in a ``finally`` block, then reloads the
    middleware module so its cached ``_tracer`` re-binds against the
    restored provider. Without the restore, later test modules sharing
    the same xdist worker would inherit this fixture's mutated globals.
    """
    original_provider = cast(Any, trace._TRACER_PROVIDER)
    original_done = trace._TRACER_PROVIDER_SET_ONCE._done

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # OTel forbids overriding via the public API; reach behind it for the
    # test environment so a real provider replaces the no-op one cached
    # at module import. This is a documented escape hatch in the SDK
    # source.
    trace._TRACER_PROVIDER_SET_ONCE._done = False
    trace._TRACER_PROVIDER = None
    trace.set_tracer_provider(provider)
    # Re-import the middleware module so its module-level
    # ``_tracer = trace.get_tracer(__name__)`` re-binds against the
    # newly-installed provider.
    import synthorg.api.middleware as mw

    importlib.reload(mw)
    try:
        yield exporter
    finally:
        provider.shutdown()
        trace._TRACER_PROVIDER = original_provider
        trace._TRACER_PROVIDER_SET_ONCE._done = original_done
        importlib.reload(mw)


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
        # ``path_template`` simulates a Litestar router that has resolved
        # the request to a low-cardinality route template before the
        # middleware sets the OTel ``http.route`` attribute. Without
        # this, ``_resolve_route_template`` returns ``__unmatched__``
        # because the synthetic scope carries no ``route_handler``.
        "path_template": "/api/v1/health",
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
    await middleware(scope, receive, send)


async def _drive_failure(middleware: Any, exc: Exception) -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "path_template": "/api/v1/health",
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
    await middleware(scope, receive, send)


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
    # ``set_status_on_exception=False`` on the span ctxmgr disables
    # OTel's default ``Status(ERROR, str(exc))`` on context exit, so
    # the description matches what the middleware passed to
    # ``set_status`` (just the type name, no exception message).
    assert span.status.description == "RuntimeError"
    attrs = dict(span.attributes or {})
    # Exception details are attached as OTel-semconv attributes (the
    # message goes through ``safe_error_description`` first) instead
    # of via ``record_exception``, which would serialise the full
    # traceback and frame locals to the OTLP exporter.
    assert attrs.get("exception.type") == "RuntimeError"
    assert "RuntimeError" in str(attrs.get("exception.message") or "")
    # No ``exception`` event should be emitted -- that's the
    # ``record_exception`` shape we deliberately avoid.
    event_names = [evt.name for evt in (span.events or ())]
    assert "exception" not in event_names
