"""Distributed tracing subsystem (OpenTelemetry, opt-in).

Exposes a narrow surface for the rest of the codebase:

* :class:`TraceConfig` (discriminated union of
  :class:`DisabledTraceConfig` and :class:`OtlpHttpTraceConfig`).
* :func:`build_trace_handler` factory keyed on ``TraceConfig.kind``.
* :class:`TraceHandler` Protocol + :class:`NoopTraceHandler` safe
  default.
* :func:`llm_span` / :func:`tool_span` context-manager helpers used by
  ``engine.loop_helpers`` and ``tools.invoker`` respectively.

The rest of the codebase never imports :mod:`opentelemetry` directly;
only this subsystem does. This keeps the OTel SDK's surface
contained and makes the pluggable-protocol boundary explicit.
"""

from synthorg.observability.tracing.config import (
    DisabledTraceConfig,
    OtlpHttpTraceConfig,
    TraceConfig,
)
from synthorg.observability.tracing.factory import build_trace_handler
from synthorg.observability.tracing.instrumentation import (
    get_tracer,
    llm_span,
    tool_span,
)
from synthorg.observability.tracing.protocol import (
    NoopTraceHandler,
    TraceHandler,
)

__all__ = [
    "DisabledTraceConfig",
    "NoopTraceHandler",
    "OtlpHttpTraceConfig",
    "TraceConfig",
    "TraceHandler",
    "build_trace_handler",
    "get_tracer",
    "llm_span",
    "tool_span",
]
