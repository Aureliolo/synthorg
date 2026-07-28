"""Background health prober service.

Periodically checks the health of all connections with
``health_check_enabled=True`` and updates their status.
"""

import asyncio
import copy
from types import MappingProxyType
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    ConnectionType,
)
from synthorg.integrations.errors import IntegrationLifecycleConflictError
from synthorg.integrations.health._probe_execution import ProbeExecutionMixin
from synthorg.integrations.health.checks.database import DatabaseHealthCheck
from synthorg.integrations.health.checks.generic_http import (
    GenericHttpHealthCheck,
)
from synthorg.integrations.health.checks.github import GitHubHealthCheck
from synthorg.integrations.health.checks.llm_provider import (
    LlmProviderHealthCheck,
    ProviderHealthLookup,
)
from synthorg.integrations.health.checks.slack import SlackHealthCheck
from synthorg.integrations.health.checks.smtp import SmtpHealthCheck
from synthorg.integrations.health.checks.tunnel import (
    TunnelHealthCheck,
    TunnelStatusLookup,
)
from synthorg.integrations.health.protocol import ConnectionHealthCheck
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_PROBER_CONFIG_INVALID,
    HEALTH_PROBER_STARTED,
    HEALTH_PROBER_STOPPED,
)

logger = get_logger(__name__)
_DEFAULT_INTERVAL_SECONDS: Final[int] = 300
_DEFAULT_UNHEALTHY_THRESHOLD: Final[int] = 3
# Above any individual checker's own budget, so a checker that bounds
# itself reports its own reason and only a genuinely stuck one trips this.
_CHECKER_TIMEOUT: Final[float] = 30.0

_CHECK_REGISTRY: Final[MappingProxyType[ConnectionType, ConnectionHealthCheck]] = (
    MappingProxyType(
        copy.deepcopy(
            {
                ConnectionType.GITHUB: GitHubHealthCheck(),
                ConnectionType.SLACK: SlackHealthCheck(),
                ConnectionType.SMTP: SmtpHealthCheck(),
                ConnectionType.DATABASE: DatabaseHealthCheck(),
                ConnectionType.GENERIC_HTTP: GenericHttpHealthCheck(),
                ConnectionType.LLM_PROVIDER: LlmProviderHealthCheck(),
                ConnectionType.TUNNEL: TunnelHealthCheck(),
                # A deploy target is a bearer-token HTTP API behind a
                # base_url, which is exactly what the generic probe
                # validates: SSRF pre-flight, authenticated request, and
                # UNHEALTHY (not false-green) on a revoked credential.
                ConnectionType.DEPLOY: GenericHttpHealthCheck(),
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
    catalog exists. Health checks that need to fetch credentials (the
    connection-bound checkers and the credential-aware generic HTTP probe)
    expose ``bind_catalog`` so the live catalog can be injected at app startup.
    """
    for checker in _CHECK_REGISTRY.values():
        bind = getattr(checker, "bind_catalog", None)
        if callable(bind):
            bind(catalog)


def bind_provider_health_lookup(lookup: ProviderHealthLookup) -> None:
    """Bind the provider-health lookup into the LLM-provider checker.

    The providers subsystem owns provider health; this hands the
    import-time checker a resolver onto the live
    ``ProviderHealthTracker`` so the Connections screen reports the
    same verdict as the Providers screen.
    """
    checker = _CHECK_REGISTRY.get(ConnectionType.LLM_PROVIDER)
    bind = getattr(checker, "bind_provider_health", None)
    if callable(bind):
        bind(lookup)


def bind_tunnel_status_lookup(lookup: TunnelStatusLookup) -> None:
    """Bind the tunnel-manager status lookup into the tunnel checker.

    The tunnel manager owns provider readiness; this hands the
    import-time checker a resolver onto the live ``TunnelManager`` so
    the Connections screen reports the same verdict as the tunnel card.
    """
    checker = _CHECK_REGISTRY.get(ConnectionType.TUNNEL)
    bind = getattr(checker, "bind_tunnel_status_lookup", None)
    if callable(bind):
        bind(lookup)


def bind_github_default_api_url(default_api_url: str) -> None:
    """Inject the operator-configured GitHub API base URL into the checker.

    The registry's :class:`GitHubHealthCheck` is built at import time with
    the public default; startup wiring resolves
    ``integrations.github_api_url`` and calls this so a GitHub Enterprise
    deployment's health probes target the operator endpoint.

    A non-``https`` value is rejected (logged, not injected): the health
    probe carries the connection's bearer token, so a plaintext endpoint
    would leak it. The secure public default stays in force.
    """
    if not default_api_url.startswith("https://"):
        logger.warning(
            HEALTH_PROBER_CONFIG_INVALID,
            setting="integrations.github_api_url",
            note="non-https github_api_url ignored; keeping secure default",
        )
        return
    checker = _CHECK_REGISTRY.get(ConnectionType.GITHUB)
    setter = getattr(checker, "set_default_api_url", None)
    if callable(setter):
        setter(default_api_url)


class HealthProberService(ProbeExecutionMixin):
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
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
        unhealthy_threshold: int = _DEFAULT_UNHEALTHY_THRESHOLD,
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
        # Eager init: ``_record_failure`` may be invoked outside the
        # probe loop (manual reporting paths), so the lock must exist
        # before the first acquire.
        self._failure_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see above.
        self._task: asyncio.Task[None] | None = None
        # Eager init: stop() must be safe before any start() call.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Survives a timed-out stop so a later start() cannot stack a
        # second probe loop on the orphaned one (canonical lifecycle
        # pattern, see docs/reference/lifecycle-sync.md).
        self._stop_failed = False

    async def start(self) -> None:
        """Start the background probe loop.

        Raises:
            IntegrationLifecycleConflictError: If a prior ``stop`` timed
                out and the prober is now unrestartable.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                logger.warning(
                    HEALTH_PROBER_STOPPED,
                    error="unrestartable after a timed-out stop",
                )
                raise IntegrationLifecycleConflictError
            if self._task is not None:
                return
            self._task = asyncio.create_task(self._probe_loop())
            logger.info(HEALTH_PROBER_STARTED, interval=self._interval)

    async def stop(self) -> None:
        """Stop the background probe loop.

        Raises:
            TimeoutError: If the probe-task drain exceeds the hard
                deadline; the prober is then marked unrestartable.
        """
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            task.cancel()

            async def _drain() -> None:
                """Await the cancelled probe task, swallowing its cancellation."""
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        HEALTH_PROBER_STOPPED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=DEFAULT_DRAIN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                self._stop_failed = True
                drain_task.cancel()
                logger.error(
                    HEALTH_PROBER_STOPPED,
                    error="stop exceeded hard deadline; prober marked unrestartable",
                    timeout_seconds=DEFAULT_DRAIN_TIMEOUT_SECONDS,
                )
                raise
            self._task = None
            logger.info(HEALTH_PROBER_STOPPED)

    async def _probe_loop(self) -> None:
        """Run probes indefinitely at the configured interval.

        Raises:
            asyncio.CancelledError: If the probe task is cancelled via
                ``stop()`` or direct task cancellation.
        """
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
        while True:
            try:
                await self._probe_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # Routine probe-loop failure: log a redacted
                # structured warning instead of ``logger.exception``
                # (full tracebacks are reserved for ``MemoryError``
                # / ``RecursionError`` per CLAUDE.md ``## Logging``).
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
                _ = tg.create_task(self._probe_one(conn.name, conn.connection_type))

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

        conn = await self._load_for_probe(name)
        if conn is None:
            return
        report = await self._run_checker(checker, conn, connection_type)
        if report is None:
            return
        await self._record_probe(conn, report)
