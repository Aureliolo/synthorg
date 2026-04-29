"""Logfire telemetry reporter.

Sends curated, privacy-validated telemetry events to the
SynthOrg-owned project on Logfire via the Logfire SDK
(OpenTelemetry compatible). The ``logfire`` package is a required
runtime dependency.

The write-only project token is **embedded** at wheel build time
via ``synthorg.telemetry.reporters._embedded_token``; operators
never configure it. Token resolution happens in
:func:`synthorg.telemetry.reporters.create_reporter` -- this class
just accepts whatever token its caller passes.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.telemetry import (
    TELEMETRY_REPORT_FAILED,
    TELEMETRY_REPORTER_INITIALIZED,
)
from synthorg.telemetry.config import DEFAULT_ENVIRONMENT
from synthorg.telemetry.reporters.errors import LogfireConfigureError

if TYPE_CHECKING:
    from synthorg.telemetry.protocol import TelemetryEvent

logger = get_logger(__name__)


class LogfireReporter:
    """Logfire SDK-based telemetry reporter.

    Args:
        token: Write-only Logfire project token (resolved by the
            factory from the embedded build constant).
        environment: Deployment-environment tag (``dev`` /
            ``pre-release`` / ``prod`` / ``ci`` / ...). Passed to
            :func:`logfire.configure` so the OTel
            ``deployment.environment`` resource attribute is set on
            every span and also included as a kwarg on every log
            record -- giving dashboards two ways to filter without
            joining on a startup event.

    Raises:
        ImportError: If the ``logfire`` package is not importable.
            Should not happen in practice -- ``logfire`` is a
            required dependency -- but propagated explicitly so the
            factory can log the real class name.
        LogfireConfigureError: If ``logfire.configure()`` fails.
    """

    def __init__(
        self,
        *,
        token: str,
        environment: str = DEFAULT_ENVIRONMENT,
    ) -> None:
        import logfire as _logfire  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415

        self._logfire = _logfire
        self._environment = environment

        try:
            # ``inspect_arguments=False`` silences the noisy
            # "Failed to introspect calling code" warning. Logfire
            # would otherwise try to introspect the source line for
            # every ``.info(event.event_type, ...)`` call to treat
            # the first positional as an f-string template. Our
            # call site passes a variable, not a literal, so
            # introspection fails on every event -- disabling it
            # is the explicit suppression the warning itself
            # suggests. ``environment=`` maps to the OTel
            # ``deployment.environment`` resource attribute.
            self._logfire.configure(
                token=token,
                send_to_logfire="if-token-present",
                service_name="synthorg-telemetry",
                service_version=_get_synthorg_version(),
                environment=environment,
                inspect_arguments=False,
            )
        except Exception as exc:
            msg = f"logfire.configure() failed: {type(exc).__name__}"
            raise LogfireConfigureError(msg) from exc

        logger.info(
            TELEMETRY_REPORTER_INITIALIZED,
            backend="logfire",
            environment=environment,
        )

    async def report(self, event: TelemetryEvent) -> None:
        """Send a telemetry event to Logfire.

        Offloads the synchronous SDK call to a thread to avoid
        blocking the event loop. Lets backend exceptions propagate
        so :meth:`TelemetryCollector._send` returns ``False`` and
        skips the misleading ``*_SENT`` success event for an
        undelivered write. :meth:`TelemetryCollector._send` owns
        the ``TELEMETRY_REPORT_FAILED`` alert -- no log here
        avoids duplicating the same metric per failure.

        ``environment`` is included both as a resource attribute
        (via :meth:`__init__`'s ``configure`` call) and as a
        per-record kwarg so dashboards can filter either way.
        """
        # Reserved kwargs we always pass explicitly. If a future
        # event's ``properties`` ever carried one of these names the
        # ``**event.properties`` unpack below would raise
        # ``TypeError`` on the duplicate kwarg; filter them out of
        # the properties payload as a belt-and-suspenders defense
        # (the :class:`PrivacyScrubber` allowlists don't currently
        # permit any of these names either, but relying on the
        # scrubber alone would couple two layers that already exist
        # to catch different classes of mistake).
        reserved = {
            "event_timestamp",
            "deployment_id",
            "synthorg_version",
            "python_version",
            "os_platform",
            "environment",
        }
        safe_properties = {
            k: v for k, v in event.properties.items() if k not in reserved
        }

        # ``**safe_properties`` intentionally carries arbitrary
        # allowlisted keys; mypy's narrowed ``to_thread`` signature
        # rejects unknown kwargs on any backend that gives
        # ``self._logfire.info`` a concrete type. When ``logfire``
        # stubs are absent (``ignore_missing_imports``), the callable
        # widens to ``Any`` and the ignore becomes unused -- hence the
        # composite suppression.
        await asyncio.to_thread(
            self._logfire.info,
            event.event_type,
            event_timestamp=event.timestamp,
            deployment_id=event.deployment_id,
            synthorg_version=event.synthorg_version,
            python_version=event.python_version,
            os_platform=event.os_platform,
            environment=event.environment,
            **safe_properties,  # type: ignore[arg-type, unused-ignore]
        )

    async def flush(self) -> None:
        """Flush the Logfire exporter."""
        try:
            await asyncio.to_thread(self._logfire.force_flush)
        except Exception as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="flush",
                error_type=type(exc).__name__,
                exc_info=True,
            )

    async def shutdown(self) -> None:
        """Flush and shut down the Logfire exporter."""
        await self.flush()
        try:
            await asyncio.to_thread(self._logfire.shutdown)
        except Exception as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="shutdown",
                error_type=type(exc).__name__,
                exc_info=True,
            )


def _get_synthorg_version() -> str:
    try:
        import synthorg  # noqa: PLC0415
    except ImportError:
        return "unknown"

    try:
        return synthorg.__version__
    except AttributeError:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="version_attribute_missing",
        )
        return "unknown"
