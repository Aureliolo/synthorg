"""Dynamic SSRF allowlist policy for provider discovery.

Provides a ``ProviderDiscoveryPolicy`` model that controls which
``host:port`` pairs are trusted for model discovery requests.  Entries
in the allowlist bypass the private-IP check, enabling discovery
against local providers (e.g. inference servers on private IPs).

The design mirrors :class:`~synthorg.tools.git_url_validator.GitCloneNetworkPolicy`.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.schema import ProviderConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_URL_ALLOWED,
)

logger = get_logger(__name__)

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_DEFAULT_PORTS: MappingProxyType[str, int] = MappingProxyType(
    {"http": 80, "https": 443},
)


class ProviderDiscoveryPolicy(BaseModel):
    """Network policy for provider discovery SSRF prevention.

    Controls which ``host:port`` pairs are trusted for model discovery
    requests.  Entries in the allowlist bypass the private-IP check,
    enabling discovery against local providers that use private IPs
    by design.

    Allowlist entries are normalized to lowercase and deduplicated
    at construction time.

    Attributes:
        host_port_allowlist: Trusted ``host:port`` pairs that bypass
            SSRF validation during model discovery.  Stored lowercase
            after construction.
        block_private_ips: Master switch for private IP blocking.
            When ``False``, **all** hosts are allowed regardless
            of IP -- use only in development.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    host_port_allowlist: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Trusted host:port pairs for discovery",
    )
    block_private_ips: bool = Field(
        default=True,
        description="Master switch for private IP blocking",
    )

    @model_validator(mode="after")
    def _normalize_allowlist(self) -> Self:
        """Lowercase and deduplicate allowlist entries."""
        normalized = tuple(dict.fromkeys(h.lower() for h in self.host_port_allowlist))
        if normalized != self.host_port_allowlist:
            object.__setattr__(self, "host_port_allowlist", normalized)
        return self


def extract_host_port(url: str) -> str | None:
    """Extract a normalized ``host:port`` pair from a URL.

    Uses the scheme-default port (80 for HTTP, 443 for HTTPS) when
    no explicit port is present.  Returns ``None`` for unparseable
    URLs or unsupported schemes.

    Args:
        url: Full URL string.

    Returns:
        Lowercase ``host:port`` string, or ``None``.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    hostname = hostname.lower()
    try:
        raw_port = parsed.port
    except ValueError:
        return None
    port = (
        raw_port
        if raw_port is not None
        else _DEFAULT_PORTS.get(
            parsed.scheme,
        )
    )
    if port is None:
        return None

    if ":" in hostname:
        return f"[{hostname}]:{port}"
    return f"{hostname}:{port}"


def seed_from_presets() -> tuple[str, ...]:
    """Extract ``host:port`` pairs from all provider preset URLs.

    Collects entries from each preset's ``candidate_urls`` and
    ``default_base_url``, deduplicates, and returns them as a tuple.

    Returns:
        Deduplicated tuple of ``host:port`` strings.
    """
    from synthorg.providers.presets import (  # noqa: PLC0415
        PROVIDER_PRESETS,
        candidate_urls_for,
    )

    seen: dict[str, None] = {}
    for preset in PROVIDER_PRESETS:
        for url in candidate_urls_for(preset):
            hp = extract_host_port(url)
            if hp is not None:
                seen.setdefault(hp, None)
        if preset.default_base_url:
            hp = extract_host_port(preset.default_base_url)
            if hp is not None:
                seen.setdefault(hp, None)
    return tuple(seen)


def build_seed_allowlist(
    providers: Mapping[str, ProviderConfig],
) -> tuple[str, ...]:
    """Build a seed allowlist from presets and installed providers.

    Merges ``host:port`` entries from preset candidate URLs with
    entries extracted from installed provider ``base_url`` values.

    Args:
        providers: Mapping of provider name to config.

    Returns:
        Deduplicated tuple of ``host:port`` strings.
    """
    seen: dict[str, None] = {}
    for hp in seed_from_presets():
        seen.setdefault(hp, None)

    for config in providers.values():
        if config.base_url is not None:
            provider_hp = extract_host_port(config.base_url)
            if provider_hp is not None:
                seen.setdefault(provider_hp, None)

    return tuple(seen)


def is_url_allowed(url: str, policy: ProviderDiscoveryPolicy) -> bool:
    """Check whether a URL is trusted by the discovery policy.

    Returns ``True`` when the URL's ``host:port`` is in the
    allowlist, or when ``block_private_ips`` is disabled (dev mode).

    Args:
        url: URL to check.
        policy: Current discovery policy.

    Returns:
        ``True`` if the URL is trusted for discovery.
    """
    if not policy.block_private_ips:
        logger.warning(
            PROVIDER_DISCOVERY_URL_ALLOWED,
            url=url,
            allowed=True,
            reason="block_private_ips_disabled",
        )
        return True

    hp = extract_host_port(url)
    if hp is None:
        return False

    allowed = hp in policy.host_port_allowlist
    logger.debug(
        PROVIDER_DISCOVERY_URL_ALLOWED,
        url=url,
        host_port=hp,
        allowed=allowed,
    )
    return allowed
