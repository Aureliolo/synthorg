"""Trace handler protocol and no-op implementation.

A :class:`TraceHandler` owns the OpenTelemetry ``TracerProvider``
lifecycle -- initialisation, span batching, and shutdown. The rest
of the codebase obtains tracers via :func:`get_tracer` and records
spans via the helpers in :mod:`...instrumentation`.

The handler is a **singleton** per process; there is no reason to
run multiple tracer providers concurrently, and mixing providers
produces spans that never correlate in the backend's waterfall view.
Startup wiring constructs the handler once and stashes the instance
on ``AppState``.
"""

from typing import TYPE_CHECKING, Protocol

from opentelemetry import trace
from opentelemetry.trace import NoOpTracer

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer


# audit-keep 2026-05-10: NoopTraceHandler impl in this file + OtlpTraceHandler
# impl in otlp_trace_handler.py + tracing/factory.py + AppState wiring.
class TraceHandler(Protocol):
    """Interface for the tracing subsystem's process-singleton handler.

    ``get_tracer`` returns an OTel :class:`~opentelemetry.trace.Tracer`
    bound to the handler's provider. ``force_flush`` blocks until the
    exporter's queue drains (used at shutdown and tests).
    ``shutdown`` stops the exporter and reclaims resources.
    """

    def get_tracer(self, name: str) -> Tracer:
        """Return a tracer identified by instrumentation library *name*."""
        ...

    async def force_flush(
        self,
        timeout_sec: float = 5.0,  # lint-allow: magic-numbers -- default.
    ) -> None:
        """Block until pending spans are exported or the deadline fires."""
        ...

    async def shutdown(self) -> None:
        """Flush and stop the exporter; the handler is unusable afterwards."""
        ...


class NoopTraceHandler:
    """Tracing disabled. Handlers return OTel's built-in NoOpTracer.

    No global ``TracerProvider`` is installed, so any third-party
    library that reads ``opentelemetry.trace.get_tracer_provider()``
    also gets a no-op. Zero allocation per span.
    """

    _TRACER = NoOpTracer()

    def get_tracer(self, name: str) -> Tracer:  # noqa: ARG002
        """Return OTel's no-op tracer regardless of *name*."""
        return self._TRACER

    async def force_flush(
        self,
        timeout_sec: float = 5.0,  # noqa: ARG002  # lint-allow: magic-numbers
    ) -> None:
        """No-op: there is no exporter to flush."""
        return

    async def shutdown(self) -> None:
        """No-op."""
        return


__all__ = ["NoopTraceHandler", "TraceHandler", "trace"]
