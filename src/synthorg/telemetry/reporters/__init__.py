"""Reporter factory for telemetry backends.

Telemetry enable is resolved upstream (the collector reads
``telemetry.enabled`` via ``ConfigResolver`` before calling this
factory). When invoked, the factory's job is to materialise a
working reporter for the configured backend or fail loudly with a
precise reason -- never to silently fall back to the noop reporter
on an unknown error class.

Two legitimate failure modes are handled here with precise except
arms (each emits one WARNING with the real ``error_type`` and
returns :class:`NoopReporter`):

* ``ImportError`` -- ``logfire`` package not installable in this
  environment. Should not occur in practice; ``logfire`` is a
  required runtime dep.
* ``LogfireConfigureError`` -- ``logfire.configure()`` rejected
  the token (network, auth, or SDK-level config issue).

A third condition -- ``is_token_embedded() == False`` -- returns
:class:`NoopReporter` silently. The operator-facing log for that
state lives in :meth:`TelemetryCollector.start` so the missing-
token condition produces exactly one startup signal instead of
duplicating across factory and collector.

Anything else propagates so silent fallback never hides a
programming bug -- which is precisely how the previous
``except Exception`` arm let two months of broken telemetry
slip past detection.
"""

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.telemetry import TELEMETRY_REPORT_FAILED
from synthorg.telemetry.config import TelemetryBackend, TelemetryConfig
from synthorg.telemetry.protocol import TelemetryReporter
from synthorg.telemetry.reporters._embedded_token import (
    EMBEDDED_TELEMETRY_TOKEN,
    is_token_embedded,
)
from synthorg.telemetry.reporters.errors import LogfireConfigureError
from synthorg.telemetry.reporters.noop import NoopReporter

logger = get_logger(__name__)


def create_reporter(config: TelemetryConfig) -> TelemetryReporter:
    """Create a telemetry reporter from configuration.

    Returns :class:`NoopReporter` when the backend is set to
    ``noop`` or when the Logfire reporter cannot be initialised.
    ``ImportError`` and ``LogfireConfigureError`` each emit one
    WARNING with the real ``error_type``; the missing-embedded-
    token branch is intentionally silent here so
    :meth:`TelemetryCollector.start` remains the single emitter
    of the operator-facing ``TELEMETRY_TOKEN_MISSING`` ERROR.

    Args:
        config: Telemetry configuration (already filtered for
            "is enabled" by the collector).

    Returns:
        A concrete ``TelemetryReporter`` implementation.

    Raises:
        ValueError: If the backend is not recognised.
    """
    if not config.enabled or config.backend == TelemetryBackend.NOOP:
        return NoopReporter()

    if config.backend == TelemetryBackend.LOGFIRE:
        if not is_token_embedded():
            # Stay quiet: ``TelemetryCollector.start()`` owns the
            # operator-facing ``TELEMETRY_TOKEN_MISSING`` ERROR. Logging
            # here too would emit two startup records for a single
            # missing-token condition and break the "exactly one
            # startup signal" contract this PR is enforcing.
            return NoopReporter()

        try:
            from synthorg.telemetry.reporters.logfire import (  # noqa: PLC0415
                LogfireReporter,
            )

            return LogfireReporter(
                token=EMBEDDED_TELEMETRY_TOKEN,
                environment=config.environment,
            )
        except ImportError as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="logfire_import_failed",
                error_type=type(exc).__name__,
            )
            return NoopReporter()
        except LogfireConfigureError as exc:
            # ``LogfireConfigureError`` is the sanitised wrapper raised
            # at the boundary; the underlying SDK chain is intentionally
            # NOT attached here. Per the project logging-hygiene rule,
            # degraded paths emit a scrubbed description instead of a
            # traceback.
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="logfire_configure_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return NoopReporter()

    msg = f"Unknown telemetry backend: {config.backend!r}"  # type: ignore[unreachable]
    raise ValueError(msg)
