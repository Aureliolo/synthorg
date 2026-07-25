# module-kind: code
"""Probe-target eligibility gates for the provider health prober.

Split from ``health_prober.py`` to keep that lifecycle orchestrator under its
module-size budget. Unlike ``health_prober_helpers`` these gates perform I/O
(DNS resolution for connection pinning), so they live in their own module
rather than alongside the pure URL/header utilities.
"""

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_HEALTH_PROBE_SKIPPED
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    is_url_allowed,
    resolve_discovery_target,
)
from synthorg.providers.health_prober_helpers import build_ping_url
from synthorg.tools.network_validator import DnsValidationOk

logger = get_logger(__name__)


async def resolve_probe_target(
    name: str,
    config: ProviderConfig,
    policy: ProviderDiscoveryPolicy | None,
    *,
    ollama_port: int,
) -> tuple[bool, DnsValidationOk | None]:
    """Run the reachability + SSRF gates for one provider.

    Shared by the periodic sweep and the on-demand single-provider probe so
    both refuse exactly the same URLs. Every rejection is logged: a silently
    skipped provider is indistinguishable from a healthy idle cycle, which is
    the failure mode that hides a mis-scoped allowlist.

    Args:
        name: Provider name.
        config: Provider configuration.
        policy: Discovery policy gating probe URLs, or ``None`` when the
            prober was wired without one (no SSRF gate applies).
        ollama_port: Resolved ``providers.ollama_default_port``.

    Returns:
        ``(eligible, validation)``. ``eligible`` is False when a gate rejected
        the probe URL; ``validation`` carries the IPs to pin the connection to
        when a policy resolved the target.
    """
    if config.base_url is None:
        # Cloud providers expose no lightweight ping, so their health comes
        # from real API call outcomes instead.
        logger.debug(
            PROVIDER_HEALTH_PROBE_SKIPPED,
            provider=name,
            reason="no_base_url",
        )
        return False, None
    url = build_ping_url(
        config.base_url, config.litellm_provider, ollama_port=ollama_port
    )
    if policy is None:
        return True, None
    if not is_url_allowed(url, policy):
        # Skip -- SSRF-blocked providers are in an indeterminate state, not a
        # failed one. UNKNOWN (zero records) is the correct status for them.
        logger.warning(
            PROVIDER_HEALTH_PROBE_SKIPPED,
            provider=name,
            reason="url_not_allowed_by_discovery_policy",
        )
        return False, None
    resolved = await resolve_discovery_target(url, policy)
    if isinstance(resolved, str):
        # An allowlisted host whose DNS will not resolve cannot be pinned;
        # probing it would reopen the rebinding window, so leave it UNKNOWN
        # rather than probe unpinned.
        logger.warning(
            PROVIDER_HEALTH_PROBE_SKIPPED,
            provider=name,
            reason="discovery_dns_unresolved",
        )
        return False, None
    return True, resolved
