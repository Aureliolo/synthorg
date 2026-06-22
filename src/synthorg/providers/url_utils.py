"""Shared URL utilities for the providers package."""

import ipaddress
from typing import Final
from urllib.parse import urlparse

from synthorg.core.url_redaction import redact_url as _redact_url

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


def is_self_url(url: str, *, backend_port: int) -> bool:
    """Check whether a URL points at the local backend.

    Compares the URL's hostname against known localhost aliases
    (and the full ``127.0.0.0/8`` + ``::1`` loopback ranges via
    ``ipaddress``) and its port against the backend's configured port.

    Args:
        url: URL to check.
        backend_port: The port the SynthOrg backend listens on.

    Returns:
        True if the URL targets the backend, False otherwise.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return False
    if hostname is None or port is None:
        return False
    if port != backend_port:
        return False
    normalized_host = hostname.rstrip(".").lower()
    if normalized_host in LOCALHOST_ALIASES:
        return True
    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


def redact_url(url: str) -> str:
    """Strip userinfo and query parameters from a URL for safe logging.

    Thin adapter over :func:`synthorg.core.url_redaction.redact_url` that
    pins the providers policy: userinfo stripped, a present query replaced
    with ``<redacted>``.

    Args:
        url: URL to redact.

    Returns:
        URL with userinfo stripped and query replaced with
        ``<redacted>`` (if present).
    """
    return _redact_url(url, query="redact")
