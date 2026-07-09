# module-kind: code
"""Classify a URL host as locally-hosted (loopback / private / localhost alias).

Shared by the providers layer (health probing, self-URL detection) and the
template model matcher (prefer a free local model over a paid remote), so it
sits in ``core`` and keeps both importers on the foundation layer with no
cross-subsystem edge.
"""

import ipaddress
from typing import Final
from urllib.parse import urlparse

from synthorg.core.normalization import normalize_ascii_lowercase

LOCALHOST_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",  # noqa: S104 -- matching alias, not binding
        "host.docker.internal",
        "172.17.0.1",
        "::1",
    }
)


def is_local_url(url: str | None) -> bool:
    """Whether a provider base URL points at a locally-hosted backend.

    A locally-hosted provider (Ollama / LM Studio / vLLM on the operator's own
    machine or LAN) costs nothing per token, so the model matcher prefers it
    over a paid remote when it meets a role's demand. Locality is a hostname
    property: a localhost alias, or any loopback / private / link-local IP.

    Args:
        url: The provider's base URL, or ``None`` for a keyless cloud provider.

    Returns:
        True when *url* targets a local/self-hosted backend, False for a remote
        provider or an absent/unparseable URL.
    """
    if not url:
        return False
    hostname = urlparse(url).hostname
    if hostname is None:
        return False
    normalized_host = normalize_ascii_lowercase(hostname.rstrip("."))
    if normalized_host in LOCALHOST_ALIASES:
        return True
    try:
        ip = ipaddress.ip_address(normalized_host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local
