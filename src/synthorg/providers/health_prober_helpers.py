# module-kind: code
"""Pure helpers for the provider health prober.

Stateless URL/header/truncation utilities split out of
``health_prober.py`` to keep that orchestrator under its module-size
budget. No I/O or lifecycle state lives here.
"""

import asyncio
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_PROBE_SKIPPED,
    PROVIDER_HEALTH_PROBER_RESOLVE_FAILED,
)
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_MAX_ERROR_MESSAGE_LENGTH: Final[int] = 200


def build_ping_url(
    base_url: str,
    litellm_provider: str | None,
    *,
    ollama_port: int,
) -> str:
    """Build a lightweight ping URL for a provider.

    Uses the cheapest possible endpoint -- no model loading.
    Providers whose ``litellm_provider`` is ``"ollama"`` (or whose
    URL is bound to ``ollama_port``) use the root URL; all others
    append ``/models``.

    Args:
        base_url: Provider base URL.
        litellm_provider: LiteLLM provider identifier for path selection.
        ollama_port: Port used to detect a self-hosted Ollama provider
            when ``litellm_provider`` is not set explicitly. Required
            (no default) so the canonical value flows through from the
            registered ``providers.ollama_default_port`` setting at
            every call site instead of mirroring it locally. Must be a
            valid TCP port (1-65535); the registry entry validates the
            bounds at write time, so a value out of range cannot reach
            this function via the resolver path.

    Returns:
        URL to ping.

    Raises:
        ValueError: ``ollama_port`` is outside the valid TCP-port range.
    """
    if not 1 <= ollama_port <= 65535:  # noqa: PLR2004 -- TCP port range
        msg = f"ollama_port must be in 1-65535, got {ollama_port!r}"
        raise ValueError(msg)
    stripped = strip_trailing_slash(base_url)
    is_ollama = litellm_provider == "ollama" or urlparse(stripped).port == ollama_port
    if is_ollama:
        return stripped  # Root URL returns a liveness string
    return f"{stripped}/models"


def build_auth_headers(
    auth_type: str,
    api_key: str | None,
) -> dict[str, str]:
    """Build auth headers for the probe request.

    Only ``api_key`` and ``subscription`` auth types produce an
    ``Authorization: Bearer`` header.  Other types (oauth,
    custom_header, none) result in no probe auth headers.

    Args:
        auth_type: Provider auth type.
        api_key: API key (may be None for local providers).

    Returns:
        Headers dict (may be empty).
    """
    if api_key and auth_type in ("api_key", "subscription"):
        return {"Authorization": f"Bearer {api_key}"}
    return {}


@dataclass(frozen=True)
class ProbeIdentity:
    """The configuration a health verdict is a statement about.

    A health check carries the configuration snapshot it started with for
    as long as the request is in flight, and several entry points can be
    in flight at once: the periodic sweep, the immediate probe a provider
    mutation triggers, and an operator's connection test. Without an
    identity to compare, the loser of that race records last, so a
    verdict about configuration the operator has already replaced becomes
    the reported health state until the next sweep.

    The URL alone does not settle it. Rotating a credential, repointing a
    connection or switching auth type all change what the provider would
    answer while leaving the address untouched, so a verdict from before
    the change says nothing about the provider after it.

    Attributes:
        url: Where the request went; ``None`` for a provider configured
            without a base URL, whose driver addresses its hosted API.
        auth_type: How the request authenticated.
        connection_name: Which stored connection supplied the credential;
            repointing it swaps the credential without touching the rest.
    """

    url: str | None
    auth_type: str
    connection_name: str | None


def _identity(config: ProviderConfig, url: str | None) -> ProbeIdentity:
    """Bind *url* to the authentication a request to it would carry.

    Returns:
        The identity a verdict about that request is about.
    """
    return ProbeIdentity(
        url=url,
        auth_type=str(config.auth_type),
        connection_name=config.connection_name,
    )


def ping_identity(config: ProviderConfig, *, ollama_port: int) -> ProbeIdentity | None:
    """The identity of a reachability ping against *config*.

    Args:
        config: Provider configuration the ping would use.
        ollama_port: Resolved ``providers.ollama_default_port``, the other
            input to the ping URL: for a provider with no explicit
            ``litellm_provider`` it alone decides root versus ``/models``.

    Returns:
        The identity, or ``None`` for a provider with no base URL, which
        the sweep does not ping at all.
    """
    if config.base_url is None:
        return None
    url = build_ping_url(
        config.base_url, config.litellm_provider, ollama_port=ollama_port
    )
    return _identity(config, url)


def call_identity(config: ProviderConfig) -> ProbeIdentity:
    """The identity of a real completion call against *config*.

    The connection test issues a driver call rather than a ping, so it is
    addressed at the base URL itself.

    Returns:
        The identity a connection-test verdict is about.
    """
    return _identity(config, config.base_url)


async def ping_identity_still_current(
    name: str,
    identity: ProbeIdentity,
    *,
    config_resolver: ConfigResolver,
) -> bool:
    """Re-read the live config and report whether *identity* still holds.

    Args:
        name: Provider name.
        identity: What the in-flight ping was a statement about.
        config_resolver: Source of the live provider configs and port.

    Returns:
        True when the live configuration still yields *identity*.
    """
    live = await config_resolver.get_provider_configs()
    live_port = await config_resolver.get_int("providers", "ollama_default_port")
    config = live.get(name)
    if config is None:
        return False
    return ping_identity(config, ollama_port=live_port) == identity


async def call_identity_still_current(
    name: str,
    identity: ProbeIdentity,
    *,
    config_resolver: ConfigResolver,
) -> bool:
    """Re-read the live config and report whether *identity* still holds.

    Args:
        name: Provider name.
        identity: What the completed call was a statement about.
        config_resolver: Source of the live provider configs.

    Returns:
        True when the live configuration still yields *identity*.
    """
    live = await config_resolver.get_provider_configs()
    config = live.get(name)
    if config is None:
        return False
    return call_identity(config) == identity


async def resolve_probe_interval(
    config_resolver: ConfigResolver,
    *,
    fallback: int,
) -> int:
    """Resolve the probe cadence an operator has set.

    Read per cycle rather than captured at construction, so widening or
    narrowing the cadence takes effect at the next cycle instead of the next
    restart. A settings-backend outage must not silently stop the sweep, so
    any resolver failure keeps *fallback*, as does a value below one second,
    which would spin the loop.

    Args:
        config_resolver: Resolver for ``providers.health_probe_interval_seconds``.
        fallback: Cadence to keep when the setting cannot be read or is unusable.

    Returns:
        Seconds between probe cycles.

    Raises:
        asyncio.CancelledError: Propagated from the resolver when the task is
            cancelled.
    """
    try:
        value = await config_resolver.get_int(
            SettingNamespace.PROVIDERS.value, "health_probe_interval_seconds"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.debug(
            PROVIDER_HEALTH_PROBER_RESOLVE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=fallback,
        )
        return fallback
    return value if value >= 1 else fallback


async def probed_within_interval(
    health_tracker: ProviderHealthTracker,
    name: str,
    *,
    interval: int,
    clock: Clock,
) -> bool:
    """Whether *name* was probed recently enough to skip this cycle.

    Args:
        health_tracker: Where the last recorded check is read from.
        name: Provider to consider.
        interval: Seconds a recorded check stays fresh for.
        clock: Time seam; also supplies the tracker's reference time.

    Returns:
        True when a recorded check is newer than *interval*.
    """
    # One time source for both the tracker's window and the elapsed
    # arithmetic: reading the summary on wall time while measuring against the
    # seam makes an injected clock silently empty the window instead of moving
    # the deadline.
    now = clock.now()
    summary = await health_tracker.get_summary(name, now=now)
    if summary.last_check_timestamp is None:
        return False
    elapsed = (now - summary.last_check_timestamp).total_seconds()
    if elapsed >= interval:
        return False
    logger.debug(
        PROVIDER_HEALTH_PROBE_SKIPPED,
        provider=name,
        seconds_since_last=round(elapsed),
    )
    return True


def truncate(msg: str, limit: int = _MAX_ERROR_MESSAGE_LENGTH) -> str:
    """Truncate a string to *limit* characters.

    Returns:
        *msg* unchanged when within *limit*, otherwise truncated to
        *limit* characters.
    """
    ellipsis = "..."
    if len(msg) <= limit:
        return msg
    if limit < len(ellipsis):
        # The ``...`` suffix cannot fit within a sub-3 limit without
        # exceeding the cap, so hard-truncate to exactly *limit* to
        # preserve the length contract.
        return msg[:limit]
    return msg[: limit - len(ellipsis)] + ellipsis
