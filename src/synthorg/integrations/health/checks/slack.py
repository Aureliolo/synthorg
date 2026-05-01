"""Slack API health check."""

import time
from datetime import UTC, datetime

import httpx

from synthorg.integrations.connections.catalog import ConnectionCatalog  # noqa: TC001
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_CHECK_PASSED,
)

logger = get_logger(__name__)

_TIMEOUT = 10.0


class SlackHealthCheck:
    """Health check via ``auth.test`` on the Slack API.

    Args:
        catalog: Connection catalog used to resolve the Slack token
            at check time. ``None`` means the checker cannot
            authenticate (returns UNKNOWN).
    """

    def __init__(self, catalog: ConnectionCatalog | None = None) -> None:
        self._catalog = catalog

    def bind_catalog(self, catalog: ConnectionCatalog) -> None:
        """Bind a catalog after construction.

        The check registry is instantiated at import time before the
        catalog exists, so it is injected afterwards via
        :func:`bind_health_check_catalog`.
        """
        self._catalog = catalog

    async def check(self, connection: Connection) -> HealthReport:
        """Verify the Slack token is valid via auth.test."""
        now = datetime.now(UTC)
        if self._catalog is None:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="catalog not bound, cannot fetch token",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                error_detail="catalog not bound",
                checked_at=now,
            )

        # ``get_credentials`` can raise. Domain / runtime failures
        # (secret backend outage, malformed row, etc.) are converted
        # to an UNHEALTHY health-check result rather than propagating
        # out of the check, since a raise would cancel any sibling
        # probes running in the same ``TaskGroup``. System-level
        # failures (``MemoryError`` / ``RecursionError``) are
        # intentionally re-raised below so they DO unwind the group;
        # they signal interpreter-wide problems that should not be
        # masked as a single connection's "unhealthy" report (#1682).
        try:
            credentials = await self._catalog.get_credentials(connection.name)
        except MemoryError, RecursionError:
            # System-level failures must propagate so the surrounding
            # TaskGroup can unwind cleanly; converting them to an
            # UNHEALTHY report would mask the real problem and leave
            # sibling probes running on a doomed process (project
            # convention; #1682, CodeRabbit at slack.py:80).
            raise
        except Exception as exc:
            # SEC-1 (#1682): the secret-backend exception text can
            # carry encrypted token blobs; scrub before logging /
            # surfacing.
            scrubbed = safe_error_description(exc)
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="credential_resolution_failed",
                error_type=type(exc).__name__,
                error=scrubbed,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=f"credential resolution failed: {scrubbed}",
                checked_at=now,
            )
        token = credentials.get("token")
        if not token:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="missing Slack token",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail="missing Slack token",
                checked_at=now,
            )

        return await self._call_auth_test(connection, token)

    async def _call_auth_test(
        self,
        connection: Connection,
        token: str,
    ) -> HealthReport:
        """Execute the ``auth.test`` call and interpret the response."""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            elapsed = (time.monotonic() - start) * 1000
            scrubbed = safe_error_description(exc)
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error_type=type(exc).__name__,
                error=scrubbed,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=scrubbed,
                checked_at=datetime.now(UTC),
            )

        elapsed = (time.monotonic() - start) * 1000
        if resp.is_error:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="Slack HTTP error",
                status_code=resp.status_code,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=f"Slack HTTP {resp.status_code}",
                checked_at=datetime.now(UTC),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="invalid_json",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail="invalid JSON from Slack",
                checked_at=datetime.now(UTC),
            )
        # ``resp.json()`` returns whatever the payload parses as -- a
        # scalar or a list will not raise ``ValueError`` but will
        # blow up on the next ``data.get("ok")`` call. Guard the
        # shape explicitly so a malformed 2xx response stays a
        # structured health failure.
        if not isinstance(data, dict):
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="Slack auth.test returned non-object JSON",
                response_type=type(data).__name__,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail="Slack auth.test returned non-object JSON",
                checked_at=datetime.now(UTC),
            )
        if data.get("ok"):
            logger.info(
                HEALTH_CHECK_PASSED,
                connection_name=connection.name,
                latency_ms=elapsed,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.HEALTHY,
                latency_ms=elapsed,
                checked_at=datetime.now(UTC),
            )
        slack_error = data.get("error", "unknown")
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=connection.name,
            error="Slack auth.test returned ok=false",
            slack_error=slack_error,
        )
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.UNHEALTHY,
            latency_ms=elapsed,
            error_detail=f"Slack auth.test: {slack_error}",
            checked_at=datetime.now(UTC),
        )
