"""Outbound SSRF pre-flight for notification webhooks.

Thin wrapper over the shared :mod:`synthorg.tools.ssrf` validator that
pins the fail-closed warning to the notification audit channel. The ntfy
and Slack sinks both POST to an operator-configured URL, so they route
through the same hardened DNS-resolution + pinned-transport path as every
other HTTP egress site.
"""

from synthorg.observability.events.notification import NOTIFICATION_SINK_CONFIG_INVALID
from synthorg.tools.network_validator import DnsValidationOk, NetworkPolicy
from synthorg.tools.ssrf import (
    build_pinned_transport,
    validate_outbound_url_scheme,
)
from synthorg.tools.ssrf import (
    resolve_outbound_target as _resolve_outbound_target,
)

__all__ = [
    "build_pinned_transport",
    "resolve_outbound_target",
    "validate_outbound_url_scheme",
]


async def resolve_outbound_target(
    url: str,
    *,
    field: str,
    policy: NetworkPolicy,
) -> DnsValidationOk:
    """Notification-scoped SSRF pre-flight (see :mod:`synthorg.tools.ssrf`).

    Returns:
        The ``DnsValidationOk`` for an accepted target.

    Raises:
        ValueError: When the URL is rejected by the SSRF policy.
    """
    return await _resolve_outbound_target(
        url,
        field=field,
        policy=policy,
        log_event=NOTIFICATION_SINK_CONFIG_INVALID,
    )
