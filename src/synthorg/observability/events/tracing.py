"""Tracing lifecycle and configuration event constants.

Distinct namespace from :mod:`synthorg.observability.events.metrics`
so trace-handler initialization and trace-config validation events
have a stable, dedicated home instead of riding on the metrics
collector's taxonomy.
"""

from typing import Final

# Trace handler lifecycle
TRACE_HANDLER_INITIALIZED: Final[str] = "trace.handler.initialized"
TRACE_HANDLER_SINGLETON_VIOLATION: Final[str] = "trace.handler.singleton_violation"
TRACE_HANDLER_GLOBAL_PROVIDER_COLLISION: Final[str] = (
    "trace.handler.global_provider_collision"
)
TRACE_HANDLER_ORPHAN_SHUTDOWN_FAILED: Final[str] = (
    "trace.handler.orphan_shutdown_failed"
)

# Trace configuration validation events
TRACE_CONFIG_INVALID_SAMPLING_RATIO: Final[str] = "trace.config.invalid_sampling_ratio"
TRACE_CONFIG_UNSUPPORTED_VARIANT: Final[str] = "trace.config.unsupported_variant"

# Span attribute mutation failures (non-fatal tracing degradation)
SPAN_ATTRIBUTE_WRITE_FAILED: Final[str] = "trace.span.attribute_write_failed"
