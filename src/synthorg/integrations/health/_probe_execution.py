"""Per-connection probe execution for :class:`HealthProberService`.

Loading the connection, running its checker under a deadline, recording
the verdict and classifying it against the failure thresholds live in
this mixin so the prober module keeps the registry, the lifecycle and
the loop. Every step is failure-isolated: probes share one
``TaskGroup``, so an exception that escapes here cancels every sibling
probe rather than just its own.

The mixin reaches back into the host service for its catalog, clock and
failure state; the ``TYPE_CHECKING`` block declares that surface so
``mypy`` type-checks the mixin in isolation.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    ConnectionType,
    HealthReport,
)
from synthorg.integrations.health.protocol import ConnectionHealthCheck
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_STATUS_TRANSITIONED,
)

if TYPE_CHECKING:
    from synthorg.integrations.connections.catalog import ConnectionCatalog

logger = get_logger(__name__)

# Above any individual checker's own budget, so a checker that bounds
# itself reports its own reason and only a genuinely stuck one trips this.
_CHECKER_TIMEOUT: Final[float] = 30.0


class ProbeExecutionMixin:
    """Probe-execution helper methods mixed into ``HealthProberService``."""

    if TYPE_CHECKING:
        _catalog: ConnectionCatalog
        _clock: Clock
        _failure_lock: asyncio.Lock
        _failure_counts: dict[str, int]
        _unhealthy_threshold: int
        _degraded_threshold: int

    async def _load_for_probe(self, name: str) -> Connection | None:
        """Re-read the connection the probe is about to check.

        Returns:
            The connection, or ``None`` when it cannot be probed and the
            reason has been logged.
        """
        # Wrap the catalog load in its own try/except so a transient
        # backend error cannot cancel sibling probes through a shared
        # ``TaskGroup``.
        try:
            conn = await self._catalog.get(name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Routine catalog-load failure: redacted warning, not
            # full traceback (see _probe_loop comment).
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="catalog.get failed",
            )
            return None
        if conn is None:
            logger.debug(
                HEALTH_CHECK_FAILED,
                connection_name=name,
                error="connection vanished between list and get",
            )
        return conn

    @staticmethod
    async def _run_checker(
        checker: ConnectionHealthCheck,
        conn: Connection,
        connection_type: ConnectionType,
    ) -> HealthReport | None:
        """Run one checker under its deadline.

        Returns:
            The report, or ``None`` when the checker failed and the
            reason has been logged.
        """
        try:
            # Probes share one task group, which returns only once every
            # child has, so a checker that never completes stalls the cycle
            # for every other connection rather than just its own. The
            # ceiling is per-checker defence: a checker that bounds itself
            # simply never reaches it.
            return await asyncio.wait_for(checker.check(conn), _CHECKER_TIMEOUT)
        except TimeoutError:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=conn.name,
                connection_type=str(connection_type),
                reason="health checker exceeded its deadline",
                timeout_seconds=_CHECKER_TIMEOUT,
            )
            return None
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Routine checker failure: redacted warning, not full
            # traceback (see _probe_loop comment).
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=conn.name,
                connection_type=str(connection_type),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="health checker raised unexpected exception",
            )
            return None

    async def _record_probe(self, conn: Connection, report: HealthReport) -> None:
        """Persist the verdict, then log any status transition it made."""
        name = conn.name
        old_status = conn.health.status
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Routine catalog-write failure: redacted warning, not
            # full traceback (see _probe_loop comment).
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
        once ``unhealthy_threshold`` is hit.

        Returns:
            The new ``ConnectionStatus`` after applying the failure-count
            thresholds. ``UNKNOWN`` reports (the checker cannot probe) pass
            through untouched: neither a success nor a failure.
        """
        async with self._failure_lock:
            if report_status == ConnectionStatus.UNKNOWN:
                # A checker with nothing to probe (e.g. a cloud LLM provider
                # whose inference routes through litellm with no base_url)
                # reports UNKNOWN. Counting it as a failure would escalate a
                # perfectly healthy provider to UNHEALTHY over successive
                # cycles, so leave the counter untouched and report UNKNOWN.
                return ConnectionStatus.UNKNOWN
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
