# module-kind: service
"""Settings-backed wiring for capability-source ingest.

Builds the ingest service from live application state: the persisted score
and status repositories, the network allowlist that already gates provider
discovery, and the operator's refresh interval.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Final

import httpx

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.capability_sources.errors import CapabilityFeedTooLargeError
from synthorg.providers.capability_sources.ingest import (
    CapabilityIngestService,
    UrlGate,
)
from synthorg.providers.discovery_policy import ProviderDiscoveryPolicy, is_url_allowed
from synthorg.providers.management.allowlist import DiscoveryAllowlistManager
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.PROVIDERS.value
_REFRESH_INTERVAL_KEY = "capability_source_refresh_interval_days"

#: Default interval when the setting cannot be read. A week matches the
#: shipped default rather than falling back to "refresh constantly", which
#: would turn an unreadable setting into a traffic problem.
_DEFAULT_INTERVAL_DAYS: Final[int] = 7

#: A published leaderboard is tens of megabytes at the outside and the
#: fetch happens off the request path, so the ceiling is generous. It is
#: still a ceiling: a feed that never finishes must fail rather than hang.
_FETCH_TIMEOUT_SECONDS: Final[float] = 120.0

#: Refuse a body past this size rather than parsing it. A feed that has
#: grown an order of magnitude past what any shipped source publishes is a
#: wrong URL or a redirect to something else entirely.
_MAX_FEED_BYTES: Final[int] = 256 * 1024 * 1024


class HttpCapabilityFeedFetcher:
    """Fetches a feed over HTTPS, bounded in both time and size."""

    __slots__ = ()

    async def fetch(self, url: str) -> bytes:
        """Return the bytes at *url*.

        Returns:
            The response body.

        Raises:
            CapabilityFeedTooLargeError: When the body exceeds the size
                ceiling, which means the URL is not the feed it claims.
            httpx.HTTPError: When the fetch fails or the status is not 2xx.
        """
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.content
        if len(body) > _MAX_FEED_BYTES:
            msg = (
                f"The feed at this URL is {len(body)} bytes, past the "
                f"{_MAX_FEED_BYTES}-byte ceiling; it was not parsed."
            )
            raise CapabilityFeedTooLargeError(msg)
        return body


def _url_gate(policy: ProviderDiscoveryPolicy | None) -> UrlGate | None:
    """Return the SSRF check for operator-supplied URLs.

    Returns:
        A callable deciding whether a URL may be fetched, or ``None`` when
        no policy is loaded. ``None`` refuses every operator URL, which is
        the safe direction: the shipped feeds are unaffected because they
        skip the gate by being reviewed here rather than at runtime.
    """
    if policy is None:
        return None
    return lambda url: is_url_allowed(url, policy)


async def resolve_refresh_interval(resolver: ConfigResolver | None) -> timedelta:
    """Read how long a source may go unrefreshed.

    Returns:
        The configured interval, or the shipped default when it cannot be
        read.
    """
    if resolver is None:
        return timedelta(days=_DEFAULT_INTERVAL_DAYS)
    try:
        days = await resolver.get_int(_NAMESPACE, _REFRESH_INTERVAL_KEY)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- an unreadable cadence falls back to the
        # shipped interval, so a settings blip changes how often a feed is
        # re-read and never whether it is read at all
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=_NAMESPACE,
            key=_REFRESH_INTERVAL_KEY,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return timedelta(days=_DEFAULT_INTERVAL_DAYS)
    return timedelta(days=days)


async def build_capability_ingest_service(
    app_state: AppState,
) -> CapabilityIngestService | None:
    """Build the ingest service from live application state.

    Returns:
        The service, or ``None`` before persistence exists. Ingest has
        nowhere to put rows without it, and an anonymous or test boot
        having no score table is not a failure.
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        return None
    settings = app_state.slice(SettingsStateSlice)
    policy: ProviderDiscoveryPolicy | None = None
    if settings.config_resolver is not None and settings.settings_service is not None:
        policy = await DiscoveryAllowlistManager(
            settings_service=settings.settings_service,
            config_resolver=settings.config_resolver,
        ).load()
    return CapabilityIngestService(
        fetcher=HttpCapabilityFeedFetcher(),
        scores=backend.model_capability_scores,
        statuses=backend.capability_source_statuses,
        url_is_allowed=_url_gate(policy),
        clock=app_state.clock,
    )


__all__ = [
    "HttpCapabilityFeedFetcher",
    "build_capability_ingest_service",
    "resolve_refresh_interval",
]
