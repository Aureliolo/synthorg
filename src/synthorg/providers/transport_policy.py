# module-kind: code
"""Whether a provider credential may travel to a configured endpoint.

A provider's ``base_url`` is allowed to be cleartext, because the whole
point of the field is self-hosted inference: the shipped presets address
Ollama, LM Studio and vLLM over plain HTTP. Attaching a credential to one
of those requests is a different question, and it is the one this module
answers: a bearer token or custom auth header sent over ``http://`` is
readable by anything on the path between here and the endpoint.

The rule is transport-scoped rather than credential-scoped, so both
embedding dispatch and the health prober decide it the same way: a
credential may cross cleartext only to a target that is this machine or
its own private network, where "the path" does not leave the operator's
trust boundary. A cleartext endpoint carrying no credential is untouched.

The residual gap is deliberate: a private-network endpoint addressed by a
DNS name (``http://vllm.lan:8000``) cannot be classified without
resolving it, and resolution is not a trust decision, so such a target is
refused rather than guessed at. An operator reaches it by naming its
address or by serving it over TLS.
"""

import ipaddress
from typing import Final
from urllib.parse import urlparse

from synthorg.providers.errors import ProviderValidationError

_SECURE_SCHEME: Final[str] = "https"
_CLEARTEXT_SCHEME: Final[str] = "http"

#: Names that resolve to this machine by definition rather than by
#: configuration. ``host.docker.internal`` is Docker's reserved alias for
#: the container host, so it can no more reach a third party than
#: ``localhost`` can; the shipped self-hosted presets use both.
_LOCAL_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {"localhost", "host.docker.internal"}
)


def _is_local_target(host: str) -> bool:
    """Whether *host* names this machine or its own private network.

    Returns:
        True when a cleartext request to *host* stays inside the
        operator's trust boundary.
    """
    # A trailing dot is a fully-qualified form of the same name, and case
    # is not significant in a hostname, so neither may decide the verdict.
    normalized = host.rstrip(".").lower()
    if normalized in _LOCAL_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(normalized)
    except ValueError:
        # A DNS name that is not a known-local alias. Classifying it would
        # take a resolution whose answer can change between now and the
        # request, so it is not local as far as this decision goes.
        return False
    # IPv4-mapped IPv6 (``::ffff:127.0.0.1``) reports neither ``is_private``
    # nor ``is_loopback`` on the IPv6Address, so unwrap before asking.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback or addr.is_private or addr.is_link_local


def is_credential_safe_transport(url: str | None) -> bool:
    """Whether a credential sent to *url* stays out of a stranger's reach.

    Args:
        url: The configured endpoint, or ``None`` when none is configured
            and the driver addresses the provider's own hosted API.

    Returns:
        True when *url* may carry a credential.
    """
    if url is None:
        # Nothing is configured, so the driver addresses the provider's own
        # hosted API over its published (TLS) endpoint. There is no
        # operator-supplied target here to send a credential to.
        return True
    parsed = urlparse(url)
    if parsed.scheme == _SECURE_SCHEME:
        return True
    if parsed.scheme != _CLEARTEXT_SCHEME:
        # Every configured URL is validated to http(s) upstream, so an
        # unknown scheme here is a target this rule cannot reason about.
        return False
    try:
        host = parsed.hostname
    except ValueError:
        # A malformed authority (a bad port) makes the host unreadable, so
        # there is nothing to classify.
        return False
    return host is not None and _is_local_target(host)


def require_credential_safe_transport(url: str | None, *, field: str) -> None:
    """Refuse to send a credential over cleartext to a non-local target.

    Args:
        url: The configured endpoint the credential would be sent to.
        field: What the endpoint is, for the operator-facing message.

    Raises:
        ProviderValidationError: When *url* would carry the credential in
            the clear beyond this machine's own network.
    """
    if is_credential_safe_transport(url):
        return
    msg = (
        f"{field} is configured over http, so its credential would be sent "
        f"in cleartext to a target outside this machine's own network. "
        f"Serve the endpoint over https, or address it by its private "
        f"network address if it is self-hosted."
    )
    raise ProviderValidationError(msg)


__all__ = ["is_credential_safe_transport", "require_credential_safe_transport"]
