# module-kind: code
"""Probe-target eligibility gates for the provider health prober.

Split from ``health_prober.py`` to keep that lifecycle orchestrator under its
module-size budget. Unlike ``health_prober_helpers`` these gates perform I/O
(DNS resolution for connection pinning), so they live in their own module
rather than alongside the pure URL/header utilities.
"""

from typing import NamedTuple

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_HEALTH_PROBE_SKIPPED
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    is_url_allowed,
    resolve_discovery_target,
)
from synthorg.providers.health_prober_helpers import build_ping_url
from synthorg.providers.presets import get_preset
from synthorg.tools.network_validator import DnsValidationOk

logger = get_logger(__name__)


class ProbeTarget(NamedTuple):
    """Outcome of the probe-eligibility gates for a single provider.

    Attributes:
        eligible: Whether the provider should be probed. ``False`` means a
            gate rejected it and has already logged the reason.
        validation: The DNS pre-flight carrying the IPs to pin the connection
            to, or ``None`` when no discovery policy gates the prober.
    """

    eligible: bool
    validation: DnsValidationOk | None


def _base_url_is_required(config: ProviderConfig) -> bool:
    """Whether this provider's preset mandates an operator-supplied base URL.

    Distinguishes a cloud provider (no ping endpoint by design) from a
    self-hosted one that should have a base URL and does not.

    Returns:
        True when the originating preset sets ``requires_base_url``; False for
        a cloud preset, an unknown preset, or a provider created without one.
    """
    if config.preset_name is None:
        return False
    preset = get_preset(config.preset_name)
    return preset is not None and preset.requires_base_url


def _log_missing_base_url(name: str, config: ProviderConfig) -> None:
    """Log a skipped provider that carries no base URL, at the right level.

    A cloud provider having none is a permanent, non-actionable steady state
    that recurs every cycle, so it stays at DEBUG. A self-hosted preset having
    none is a misconfiguration whose only other symptom is a provider that
    silently never reports health, so it warrants a WARNING.
    """
    if _base_url_is_required(config):
        logger.warning(
            PROVIDER_HEALTH_PROBE_SKIPPED,
            provider=name,
            reason="base_url_required_but_missing",
            preset=config.preset_name,
        )
        return
    logger.debug(
        PROVIDER_HEALTH_PROBE_SKIPPED,
        provider=name,
        reason="no_base_url",
    )


async def resolve_probe_target(
    name: str,
    config: ProviderConfig,
    policy: ProviderDiscoveryPolicy | None,
    *,
    ollama_port: int,
) -> ProbeTarget:
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
        A :class:`ProbeTarget`; ``eligible`` is False when a gate rejected the
        probe URL.
    """
    if config.base_url is None:
        _log_missing_base_url(name, config)
        return ProbeTarget(eligible=False, validation=None)
    url = build_ping_url(
        config.base_url, config.litellm_provider, ollama_port=ollama_port
    )
    if policy is None:
        return ProbeTarget(eligible=True, validation=None)
    if not is_url_allowed(url, policy):
        # Skip -- SSRF-blocked providers are in an indeterminate state, not a
        # failed one. UNKNOWN (zero records) is the correct status for them.
        logger.warning(
            PROVIDER_HEALTH_PROBE_SKIPPED,
            provider=name,
            reason="url_not_allowed_by_discovery_policy",
        )
        return ProbeTarget(eligible=False, validation=None)
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
        return ProbeTarget(eligible=False, validation=None)
    return ProbeTarget(eligible=True, validation=resolved)
