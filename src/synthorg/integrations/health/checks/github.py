"""GitHub API health check."""

from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlparse

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.catalog import ConnectionCatalog
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

_DEFAULT_API_URL = "https://api.github.com"
"""Documented default that mirrors the ``integrations.github_api_url``
setting.  Production callers inject the resolved value via
:meth:`GitHubHealthCheck.__init__` so a GitHub Enterprise connection
without its own ``base_url`` falls through to the operator-configured
endpoint rather than the public GitHub API."""

_TIMEOUT: Final[float] = 10.0
_HTTP_OK: Final[int] = 200

# Allow-list of hostnames the GitHub health check will send a bearer
# token to. Prevents token exfiltration when a malicious operator
# points ``connection.base_url`` at a hostile endpoint. The default
# list covers github.com (cloud) plus the generic ``ghe.``/``github.``
# Enterprise prefixes we expect customers to use. Override by adding
# specific hostnames through config, not by disabling the check.
_BUILTIN_ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    "api.github.com",
    ".github.com",
    ".ghe.com",
)


def _is_allowed_github_host(
    api_url: str,
    extra_allowed_hosts: tuple[str, ...] = (),
) -> bool:
    """Return ``True`` iff ``api_url`` targets a trusted GitHub host.

    Rejects non-HTTPS schemes, empty hostnames, and hostnames that do
    not match an entry in the built-in allowlist or the per-instance
    ``extra_allowed_hosts``. A credentialed bearer token must never
    leave the process for a host that failed this check.

    Args:
        api_url: Candidate URL whose host is checked against the
            allowlist.
        extra_allowed_hosts: Per-instance trusted hosts (typically the
            operator-configured ``integrations.github_api_url`` host
            for self-hosted GitHub Enterprise / GitHub-compatible
            deployments).  Compared as exact host matches only -- no
            suffix semantics, so ``git.example.com`` does not implicitly
            trust ``evil.git.example.com``.
    """
    try:
        parsed = urlparse(api_url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in _BUILTIN_ALLOWED_HOST_SUFFIXES
    ):
        return True
    return host in extra_allowed_hosts


class GitHubHealthCheck:
    """Health check via ``GET /user`` on the GitHub API."""

    def __init__(
        self,
        catalog: ConnectionCatalog | None = None,
        *,
        default_api_url: str = _DEFAULT_API_URL,
        clock: Clock | None = None,
    ) -> None:
        # ``default_api_url`` is operator-tunable; resolve via
        # ``ConfigResolver.get_str("integrations", "github_api_url")``
        # at the call site to support GitHub Enterprise installations
        # whose connections were registered without an explicit
        # ``base_url``.
        self._catalog = catalog
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._default_api_url = default_api_url
        # Pre-compute the per-instance trusted host set so the
        # operator-configured GHE endpoint passes
        # ``_is_allowed_github_host`` without forcing every connection
        # to repeat the host as an explicit ``base_url``.
        self._trusted_default_hosts: tuple[str, ...] = self._build_default_hosts(
            default_api_url
        )

    @staticmethod
    def _build_default_hosts(default_api_url: str) -> tuple[str, ...]:
        """Extract the host of ``default_api_url`` for the instance allowlist.

        Returns an empty tuple when the URL is the public default
        (``https://api.github.com``) -- that host already passes the
        built-in allowlist, so no per-instance entry is needed.
        Returns an empty tuple as well when the URL is malformed; the
        caller's allowlist falls back to the built-in suffixes only.

        Returns:
            A one-element tuple with the lowercase hostname of
            ``default_api_url``, or an empty tuple for the public default
            or a malformed URL.
        """
        try:
            parsed = urlparse(default_api_url)
        except ValueError:
            return ()
        host = (parsed.hostname or "").lower()
        if not host:
            return ()
        return (host,)

    def bind_catalog(self, catalog: ConnectionCatalog) -> None:
        """Bind a catalog after construction (see prober registry)."""
        self._catalog = catalog

    def set_default_api_url(self, default_api_url: str) -> None:
        """Inject the operator-configured GitHub API base URL at startup.

        The check registry is instantiated at import time, before settings
        resolve, so the public default is baked in. Startup wiring resolves
        ``integrations.github_api_url`` and injects it here so a GitHub
        Enterprise connection without its own ``base_url`` falls through to
        the operator endpoint, and that host joins the per-instance bearer
        allowlist.
        """
        self._default_api_url = default_api_url
        self._trusted_default_hosts = self._build_default_hosts(default_api_url)

    async def check(self, connection: Connection) -> HealthReport:
        """Verify the GitHub token is valid via /user endpoint.

        Returns:
            A ``HealthReport``: ``HEALTHY`` on HTTP 200, ``UNHEALTHY`` on
            non-200 or network error, or ``UNKNOWN`` when the catalog is
            not bound or ``token`` is missing.
        """
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

        # ``get_credentials`` can raise (secret backend outage,
        # malformed row, etc.). Treat those as an UNHEALTHY result
        # for this connection instead of propagating -- a raise here
        # would also cancel any sibling probes running in the same
        # TaskGroup.
        try:
            credentials = await self._catalog.get_credentials(connection.name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            error_desc = safe_error_description(exc)
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                context="credential resolution failed",
                error_type=type(exc).__name__,
                error=error_desc,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=f"credential resolution failed: {error_desc}",
                checked_at=now,
            )
        token = credentials.get("token")
        if not token:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="missing GitHub token",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail="missing GitHub token",
                checked_at=now,
            )

        api_url = connection.base_url or self._default_api_url
        if not _is_allowed_github_host(api_url, self._trusted_default_hosts):
            # Fail closed: a custom ``base_url`` pointing at a non-
            # GitHub host would otherwise have the bearer token
            # exfiltrated to that host on the next request.
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="base_url not in GitHub allow-list; refusing to send token",
                api_url=api_url,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=(
                    "GitHub connection base_url is not a trusted "
                    "GitHub host; token not sent"
                ),
                checked_at=now,
            )
        url = f"{api_url}/user"
        start = self._clock.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            elapsed = (self._clock.monotonic() - start) * 1000
            if resp.status_code == _HTTP_OK:
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
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=f"GitHub API returned {resp.status_code}",
                checked_at=datetime.now(UTC),
            )
        except httpx.HTTPError as exc:
            elapsed = (self._clock.monotonic() - start) * 1000
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=safe_error_description(exc),
                checked_at=datetime.now(UTC),
            )
