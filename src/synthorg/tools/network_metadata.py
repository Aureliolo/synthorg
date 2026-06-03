"""Cloud-metadata / link-local endpoint detection for tool egress.

A narrow SSRF primitive kept out of :mod:`synthorg.tools.network_validator`
so that module stays within its size budget. Used by tools (e.g. the
headless browser) that must reach a sandbox-local app-under-test on
loopback or a private address while still refusing the cloud
instance-metadata service.
"""

import ipaddress
from typing import Final

_METADATA_HOSTNAMES: Final[frozenset[str]] = frozenset({"metadata.google.internal"})
"""Hostnames that resolve to a cloud instance-metadata service."""

_LINK_LOCAL_NETWORKS: Final[
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
] = (
    ipaddress.IPv4Network("169.254.0.0/16"),  # incl. the 169.254.169.254 IMDS
    ipaddress.IPv6Network("fe80::/10"),
)


def is_cloud_metadata_host(host: str) -> bool:
    """Check whether *host* is a link-local or cloud-metadata endpoint.

    Deliberately narrower than
    :func:`synthorg.tools.network_validator.is_blocked_ip`: loopback and
    private ranges are NOT flagged, so a sandboxed tool can still reach an
    app-under-test on ``localhost`` or a docker-network address while
    link-local metadata endpoints (``169.254.169.254``, ``fe80::``,
    ``metadata.google.internal``) are refused. Link-local space is never a
    legitimate app target, so blocking the whole ``169.254.0.0/16`` /
    ``fe80::/10`` range also covers the metadata IP without a brittle
    single-address allowlist.

    Args:
        host: Hostname or IP literal extracted from a URL.

    Returns:
        ``True`` when *host* is a metadata hostname or a link-local IP.
    """
    if host.lower() in _METADATA_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in _LINK_LOCAL_NETWORKS)
