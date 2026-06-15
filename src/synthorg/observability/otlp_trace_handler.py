"""OTLP trace handler -- exports OpenTelemetry spans via HTTP/protobuf.

Owns the process-global ``TracerProvider`` + ``BatchSpanProcessor``
+ :class:`OTLPSpanExporter`. Constructing this handler installs its
provider as the global default so third-party libraries that read
``opentelemetry.trace.get_tracer_provider()`` also emit through this
exporter.

Called from startup wiring; the rest of the codebase accesses
tracers via :func:`synthorg.observability.tracing.get_tracer` which
forwards to the global provider.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from opentelemetry import trace as _ot_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import Tracer

from synthorg.core.normalization import strip_trailing_slash
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.metrics import (
    METRICS_OTLP_EXPORT_FAILED,
    METRICS_OTLP_FLUSHER_STARTED,
    METRICS_OTLP_FLUSHER_STOPPED,
)
from synthorg.observability.events.tracing import (
    TRACE_HANDLER_GLOBAL_PROVIDER_COLLISION,
    TRACE_HANDLER_ORPHAN_SHUTDOWN_FAILED,
    TRACE_HANDLER_SINGLETON_VIOLATION,
)

if TYPE_CHECKING:
    from synthorg.observability.tracing.config import OtlpHttpTraceConfig

logger = get_logger(__name__)

_DEFAULT_FORCE_FLUSH_TIMEOUT_SECONDS: Final[float] = 5.0

_TRACES_ENDPOINT_SUFFIX = "/v1/traces"

# Process singleton: OpenTelemetry's global TracerProvider is set
# exactly once per process. A second :class:`OtlpTraceHandler`
# instance would install a fresh provider, leaving the first
# instance returning tracers from a stale handle while the rest of
# the process uses the newer provider -- spans split across the two
# and correlation breaks. ``build_trace_handler`` runs from startup
# wiring and must not be called twice.
_HANDLER_INSTANCE: OtlpTraceHandler | None = None


class OtlpTraceHandler:
    """Process-singleton OTLP trace exporter.

    Args:
        config: OTLP HTTP trace configuration.

    Raises:
        RuntimeError: A second handler cannot be constructed within
            the same process; the first instance would be orphaned.
    """

    def __init__(self, config: OtlpHttpTraceConfig) -> None:
        global _HANDLER_INSTANCE  # noqa: PLW0603
        if _HANDLER_INSTANCE is not None:
            msg = (
                "OtlpTraceHandler is a process singleton; an instance "
                "already owns the global TracerProvider. Call "
                "shutdown() and _reset_for_testing() to rebuild."
            )
            logger.error(
                TRACE_HANDLER_SINGLETON_VIOLATION,
                endpoint=config.endpoint,
                service_name=config.service_name,
                sampling_ratio=config.sampling_ratio,
                error=msg,
            )
            raise RuntimeError(msg)
        endpoint = _resolve_traces_endpoint(config.endpoint)
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers=dict(config.headers),
            # Pass the float directly: OTLPSpanExporter's ``timeout``
            # is Optional[float], and casting to ``int`` would silently
            # truncate sub-second deadlines (e.g. 0.5 -> 0 disables
            # the timeout entirely).
            timeout=config.batch_export_timeout_sec,
        )
        resource = Resource.create({"service.name": config.service_name})
        sampler = TraceIdRatioBased(config.sampling_ratio)
        self._provider = TracerProvider(resource=resource, sampler=sampler)
        self._processor = BatchSpanProcessor(
            exporter,
            max_queue_size=config.batch_max_queue_size,
            max_export_batch_size=config.batch_max_export_batch_size,
            schedule_delay_millis=int(config.schedule_delay_sec * 1000),
            export_timeout_millis=int(config.batch_export_timeout_sec * 1000),
        )
        self._provider.add_span_processor(self._processor)
        _ot_trace.set_tracer_provider(self._provider)
        # OTel's ``set_tracer_provider`` is one-shot: a default
        # provider can auto-initialise before us (env var or a lazy
        # library call that read ``get_tracer_provider``), and the
        # install silently fails. Fail loudly here so we don't run
        # with a split brain where ``self._provider`` is live but
        # the rest of the process uses a different global.
        installed = _ot_trace.get_tracer_provider()
        if installed is not self._provider:
            msg = (
                "OpenTelemetry global TracerProvider was already "
                "set before OtlpTraceHandler installed its own. "
                "Call build_trace_handler earlier in startup or "
                "unset OTEL_PYTHON_TRACER_PROVIDER / any library "
                "that auto-initialises the global provider."
            )
            logger.error(
                TRACE_HANDLER_GLOBAL_PROVIDER_COLLISION,
                endpoint=config.endpoint,
                service_name=config.service_name,
                sampling_ratio=config.sampling_ratio,
                error=msg,
            )
            # The orphaned provider + processor would otherwise keep
            # a live ``BatchSpanProcessor`` thread going. Shut the
            # provider down before propagating the error so the
            # failing construction leaves no background resources.
            try:
                self._provider.shutdown()
            except Exception as exc:  # noqa: BLE001 -- cleanup best-effort
                logger.warning(
                    TRACE_HANDLER_ORPHAN_SHUTDOWN_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            raise RuntimeError(msg)
        _HANDLER_INSTANCE = self
        logger.info(
            METRICS_OTLP_FLUSHER_STARTED,
            component="trace",
            endpoint=endpoint,
            sampling_ratio=config.sampling_ratio,
        )

    def get_tracer(self, name: str) -> Tracer:
        """Return a tracer bound to this handler's provider."""
        return self._provider.get_tracer(name)

    async def force_flush(
        self, timeout_sec: float = _DEFAULT_FORCE_FLUSH_TIMEOUT_SECONDS
    ) -> None:
        """Block until pending spans are exported or the deadline fires."""
        timeout_ms = int(timeout_sec * 1000)
        flushed = await asyncio.to_thread(
            self._provider.force_flush,
            timeout_ms,
        )
        if not flushed:
            logger.warning(
                METRICS_OTLP_EXPORT_FAILED,
                component="trace",
                reason="flush_timeout",
                timeout_sec=timeout_sec,
            )

    async def shutdown(self) -> None:
        """Flush pending spans and stop the exporter.

        Flushing first prevents the BatchSpanProcessor from dropping
        spans that are still in its queue when ``shutdown`` hands
        the exporter its final signal. The handler is unusable
        after this call returns.

        Does **not** clear :data:`_HANDLER_INSTANCE`: the global
        TracerProvider OTel installed in ``__init__`` stays wired
        for the lifetime of the process, so letting a new handler
        be constructed here would silently bypass the singleton
        guard. Tests that need to rebuild a handler call
        :func:`_reset_for_testing` explicitly.
        """
        await self.force_flush()
        await asyncio.to_thread(self._provider.shutdown)
        logger.info(METRICS_OTLP_FLUSHER_STOPPED, component="trace")


def _reset_for_testing() -> None:
    """Clear the process-singleton guard.

    Intended for tests that need to rebuild an :class:`OtlpTraceHandler`
    within the same process. Production code must not call this;
    it leaves the previous provider installed as the OTel global,
    which is exactly the state the singleton guard exists to prevent.
    """
    global _HANDLER_INSTANCE  # noqa: PLW0603
    _HANDLER_INSTANCE = None


def _resolve_traces_endpoint(base_endpoint: str) -> str:
    """Append ``/v1/traces`` to *base_endpoint* if not already present.

    Returns:
        The endpoint guaranteed to end with ``/v1/traces`` (unchanged
        when already present, otherwise the suffix appended after
        stripping a trailing slash).
    """
    if base_endpoint.endswith(_TRACES_ENDPOINT_SUFFIX):
        return base_endpoint
    return strip_trailing_slash(base_endpoint) + _TRACES_ENDPOINT_SUFFIX
