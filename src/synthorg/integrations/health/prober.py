"""Background health prober service.

Periodically checks the health of all connections with
``health_check_enabled=True`` and updates their status.
"""

import asyncio
import contextlib
import copy
from types import MappingProxyType
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.catalog import ConnectionCatalog  # noqa: TC001
from synthorg.integrations.connections.models import (
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.checks.database import DatabaseHealthCheck
from synthorg.integrations.health.checks.generic_http import (
    GenericHttpHealthCheck,
)
from synthorg.integrations.health.checks.github import GitHubHealthCheck
from synthorg.integrations.health.checks.slack import SlackHealthCheck
from synthorg.integrations.health.checks.smtp import SmtpHealthCheck
from synthorg.integrations.health.protocol import ConnectionHealthCheck  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_PROBER_CONFIG_INVALID,
    HEALTH_PROBER_STARTED,
    HEALTH_PROBER_STOPPED,
    HEALTH_STATUS_TRANSITIONED,
)

logger = get_logger(__name__)

_CHECK_REGISTRY: Final[MappingProxyType[ConnectionType, ConnectionHealthCheck]] = (
    MappingProxyType(
        copy.deepcopy(
            {
                ConnectionType.GITHUB: GitHubHealthCheck(),
                ConnectionType.SLACK: SlackHealthCheck(),
                ConnectionType.SMTP: SmtpHealthCheck(),
                ConnectionType.DATABASE: DatabaseHealthCheck(),
                ConnectionType.GENERIC_HTTP: GenericHttpHealthCheck(),
            }
        )
    )
)


def get_health_checker(
    connection_type: ConnectionType,
) -> ConnectionHealthCheck | None:
    """Return the registered checker for a connection type, if any."""
    return _CHECK_REGISTRY.get(connection_type)


def bind_health_check_catalog(catalog: ConnectionCatalog) -> None:
    """Bind a catalog to every checker that exposes ``bind_catalog``.

    The check registry is instantiated at import time, before the
    catalog exists. Health checks that need to fetch credentials
    (GitHub, Slack) expose ``bind_catalog`` so the live catalog can
    be injected at app startup.
    """
    for checker in _CHECK_REGISTRY.values():
        bind = getattr(checker, "bind_catalog", None)
        if callable(bind):
            bind(catalog)


class HealthProberService:
    """Background service that probes connection health.

    Args:
        catalog: The connection catalog to monitor.
        interval_seconds: Probe interval (default 300 = 5 min).
        unhealthy_threshold: Consecutive failures before unhealthy.
        degraded_threshold: Consecutive failures before degraded.
    """

    def __init__(
        self,
        catalog: ConnectionCatalog,
        *,
        interval_seconds: int = 300,
        unhealthy_threshold: int = 3,
        degraded_threshold: int = 1,
        clock: Clock | None = None,
    ) -> None:
        # Validate at the boundary so a misconfigured operator value
        # surfaces a clear ValueError at construction instead of
        # crashing the probe task at runtime when the negative value
        # reaches ``self._clock.sleep`` (which rejects negatives).
        if interval_seconds <= 0:
            logger.error(
                HEALTH_PROBER_CONFIG_INVALID,
                parameter="interval_seconds",
                value=interval_seconds,
            )
            msg = f"interval_seconds must be positive; got {interval_seconds}"
            raise ValueError(msg)
        if degraded_threshold <= 0:
            logger.error(
                HEALTH_PROBER_CONFIG_INVALID,
                parameter="degraded_threshold",
                value=degraded_threshold,
            )
            msg = f"degraded_threshold must be positive; got {degraded_threshold}"
            raise ValueError(msg)
        if unhealthy_threshold < degraded_threshold:
            logger.error(
                HEALTH_PROBER_CONFIG_INVALID,
                parameter="unhealthy_threshold",
                unhealthy_threshold=unhealthy_threshold,
                degraded_threshold=degraded_threshold,
            )
            msg = (
                f"unhealthy_threshold ({unhealthy_threshold}) must be "
                f">= degraded_threshold ({degraded_threshold})"
            )
            raise ValueError(msg)
        self._catalog = catalog
        self._interval = interval_seconds
        self._unhealthy_threshold = unhealthy_threshold
        self._degraded_threshold = degraded_threshold
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._failure_counts: dict[str, int] = {}
        self._failure_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the background probe loop."""
        async with self._lifecycle_lock:
            if self._task is not None:
                return
            self._task = asyncio.create_task(self._probe_loop())
            logger.info(HEALTH_PROBER_STARTED, interval=self._interval)

    async def stop(self) -> None:
        """Stop the background probe loop."""
        async with self._lifecycle_lock:
            if self._task is None:
                return
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info(HEALTH_PROBER_STOPPED)

    async def _probe_loop(self) -> None:
        """Run probes indefinitely at the configured interval."""
        while True:
            try:
                await self._probe_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Routine probe-loop failure: log a redacted structured
                # warning instead of ``logger.exception`` (SEC-1: full
                # tracebacks are reserved for ``MemoryError`` /
                # ``RecursionError`` per CLAUDE.md).
                logger.warning(
                    HEALTH_CHECK_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    reason="unexpected error in probe loop",
                )
            await self._clock.sleep(self._interval)

    async def _probe_all(self) -> None:
        """Probe all connections with health checks enabled."""
        connections = await self._catalog.list_all()
        eligible = [c for c in connections if c.health_check_enabled]
        if not eligible:
            return

        async with asyncio.TaskGroup() as tg:
            for conn in eligible:
                tg.create_task(self._probe_one(conn.name, conn.connection_type))

    async def _probe_one(
        self,
        name: str,
        connection_type: ConnectionType,
    ) -> None:
        """Probe a single connection and update its health.

        Exceptions from the checker are caught and logged here so
        one flaky probe cannot cancel its siblings inside the
        ``TaskGroup`` in ``_probe_all``.
        """
        checker = get_health_checker(connection_type)
        if checker is None:
            logger.debug(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                error="no health checker registered for type",
                connection_type=str(connection_type),
            )
            return

        # Wrap the catalog load in its own try/except so a transient
        # backend error cannot cancel sibling probes through a shared
        # ``TaskGroup``.
        try:
            conn = await self._catalog.get(name)
        except Exception:
            logger.exception(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                error="catalog.get failed",
            )
            return
        if conn is None:
            logger.debug(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                error="connection vanished between list and get",
            )
            return

        try:
            report = await checker.check(conn)
        except Exception:
            logger.exception(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                connection_type=str(connection_type),
                error="health checker raised unexpected exception",
            )
            return

        old_status = conn.health_status
        now = self._clock.now()
        new_status = await self._classify_status(name, report.status)

        # Same principle: an error inside ``update_health`` must not
        # cancel sibling TaskGroup probes either. The transition log
        # fires only after the persistence write succeeds (CLAUDE.md
        # state-transition rule: "Logs fire AFTER the persistence
        # write succeeds so the audit trail only captures transitions
        # that actually landed").
        try:
            await self._catalog.update_health(
                name,
                status=new_status,
                checked_at=now,
            )
        except Exception as exc:
            # Routine catalog-write failure: redacted warning, not
            # full traceback (SEC-1, see _probe_loop comment).
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="catalog.update_health failed",
            )
        else:
            if old_status != new_status:
                logger.info(
                    HEALTH_STATUS_TRANSITIONED,
                    connection_name=name,
                    from_status=old_status,
                    to_status=new_status,
                    checked_at=now,
                )

    async def _classify_status(
        self,
        name: str,
        report_status: ConnectionStatus,
    ) -> ConnectionStatus:
        """Update the per-name failure counter and resolve the new status.

        Honours ``degraded_threshold``: stay ``HEALTHY`` until the
        degraded threshold is reached, transition to ``DEGRADED``
        between the two thresholds, and flip to ``UNHEALTHY`` only
        once ``unhealthy_threshold`` is hit. Previously a single
        failure forced ``DEGRADED`` regardless of configuration, so
        raising ``degraded_threshold`` had no effect.
        """
        async with self._failure_lock:
            if report_status == ConnectionStatus.HEALTHY:
                self._failure_counts.pop(name, None)
                return ConnectionStatus.HEALTHY
            count = self._failure_counts.get(name, 0) + 1
            self._failure_counts[name] = count
            if count >= self._unhealthy_threshold:
                return ConnectionStatus.UNHEALTHY
            if count >= self._degraded_threshold:
                return ConnectionStatus.DEGRADED
            return ConnectionStatus.HEALTHY
