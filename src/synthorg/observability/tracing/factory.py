"""Factory that resolves :class:`TraceConfig` to a :class:`TraceHandler`.

Dispatches on the config's ``kind`` discriminator so new backends
plug in with a single ``elif`` here -- the rest of the codebase
never sees the concrete handler class.
"""

from urllib.parse import urlsplit, urlunsplit

from synthorg.observability import get_logger
from synthorg.observability.events.tracing import (
    TRACE_CONFIG_UNSUPPORTED_VARIANT,
    TRACE_HANDLER_INITIALIZED,
)
from synthorg.observability.tracing.config import (
    DisabledTraceConfig,
    OtlpHttpTraceConfig,
    TraceConfig,
)
from synthorg.observability.tracing.protocol import (
    NoopTraceHandler,
    TraceHandler,
)

logger = get_logger(__name__)


def _redact_endpoint(endpoint: str) -> str:
    """Strip credentials and query params from *endpoint* before logging.

    OTLP endpoints occasionally embed API tokens in ``userinfo`` or
    ``?token=...`` query parameters. Returning only
    ``scheme://host[:port]/path`` keeps the log useful for operators
    without leaking secrets.

    Returns:
        The endpoint reduced to ``scheme://host[:port]/path`` (no
        userinfo or query), or ``"<unparseable>"`` when the URL cannot
        be parsed.
    """
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname or ""
        # ``parts.port`` can raise ``ValueError`` on a malformed port
        # (e.g. ``http://host:bad``) -- resolve it inside the guard so
        # an unparseable port doesn't surface through the caller.
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return "<unparseable>"


def build_trace_handler(config: TraceConfig) -> TraceHandler:
    """Resolve *config* into an active :class:`TraceHandler`.

    Returns a :class:`NoopTraceHandler` when tracing is disabled (the
    helpers in :mod:`...instrumentation` become zero-cost) and a
    concrete exporter-backed handler otherwise.

    Args:
        config: A :class:`TraceConfig` discriminated union variant.

    Returns:
        The resolved handler, ready for use.

    Raises:
        ValueError: If *config* is not one of the supported variants
            (defensive guard against runtime misuse from tests or
            dynamic construction).
    """
    if isinstance(config, DisabledTraceConfig):
        logger.info(
            TRACE_HANDLER_INITIALIZED,
            component="trace",
            kind="disabled",
        )
        return NoopTraceHandler()
    if isinstance(config, OtlpHttpTraceConfig):
        # Lazy import -- OTel SDK only imported when actually
        # constructing an exporter-backed handler.
        from synthorg.observability.otlp_trace_handler import (  # noqa: PLC0415
            OtlpTraceHandler,
        )

        logger.info(
            TRACE_HANDLER_INITIALIZED,
            component="trace",
            kind=config.kind,
            endpoint=_redact_endpoint(config.endpoint),
        )
        return OtlpTraceHandler(config)
    msg = f"Unsupported TraceConfig variant: {type(config).__name__}"  # type: ignore[unreachable]
    logger.error(
        TRACE_CONFIG_UNSUPPORTED_VARIANT,
        variant=type(config).__name__,
    )
    raise ValueError(msg)
