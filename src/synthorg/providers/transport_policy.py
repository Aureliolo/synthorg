# module-kind: code
"""Whether a configured endpoint may be addressed in the clear.

A provider's ``base_url`` is allowed to be cleartext, because the whole
point of the field is self-hosted inference: the shipped presets address
Ollama, LM Studio and vLLM over plain HTTP. Leaving the operator's own
network in the clear is a different question, and it is the one this
module answers.

The rule is about the destination, not about what the request happens to
carry, because both halves of an outbound request are confidential. A
bearer token or custom auth header over ``http://`` is readable by
anything on the path; so is the text an embedding request is asking to be
embedded, which is company memory. Keying the check on the credential
alone would leave a provider that needs no credential sending memory to a
public host in cleartext.

So an explicit endpoint may be cleartext only when it names this machine
or its own private network, where "the path" does not leave the
operator's trust boundary. Everything else must use TLS. Local self-hosted
inference is untouched, which is the configuration the field exists for.

The residual gap is deliberate: a private-network endpoint addressed by a
DNS name (``http://vllm.lan:8000``) cannot be classified without
resolving it, and resolution is not a trust decision, so such a target is
refused rather than guessed at. An operator reaches it by naming its
address or by serving it over TLS.
"""

from typing import Final
from urllib.parse import urlparse

from synthorg.core.url_locality import is_local_url
from synthorg.providers.errors import ProviderValidationError

_SECURE_SCHEME: Final[str] = "https"
_CLEARTEXT_SCHEME: Final[str] = "http"


def is_confidential_transport(url: str | None) -> bool:
    """Whether a request to *url* stays out of a stranger's reach.

    Args:
        url: The configured endpoint, or ``None`` when none is configured
            and the driver addresses the provider's own hosted API.

    Returns:
        True when *url* may carry confidential content.
    """
    if url is None:
        # Nothing is configured, so the driver addresses the provider's own
        # hosted API over its published (TLS) endpoint. There is no
        # operator-supplied target here to send anything to.
        return True
    try:
        scheme = urlparse(url).scheme
    except ValueError:
        # A malformed authority (a bad port) makes the target unreadable,
        # so there is nothing to classify.
        return False
    if scheme == _SECURE_SCHEME:
        return True
    if scheme != _CLEARTEXT_SCHEME:
        # Every configured URL is validated to http(s) upstream, so an
        # unknown scheme here is a target this rule cannot reason about.
        return False
    # Locality is asked of the same classifier the rest of the system uses,
    # so "local" cannot mean one thing to the model matcher and another
    # here. A DNS name it cannot place is not local: settling it would take
    # a resolution whose answer can change before the request goes out.
    return is_local_url(url)


def require_confidential_transport(url: str | None, *, field: str) -> None:
    """Refuse to address a non-local target in the clear.

    Args:
        url: The configured endpoint the request would be sent to.
        field: What the endpoint is, for the operator-facing message.

    Raises:
        ProviderValidationError: When *url* would carry the request in the
            clear beyond this machine's own network.
    """
    if is_confidential_transport(url):
        return
    msg = (
        f"{field} is configured over http to a target outside this machine's "
        f"own network, so both its credential and the content it sends would "
        f"travel in cleartext. Serve the endpoint over https, or address it "
        f"by its private network address if it is self-hosted."
    )
    raise ProviderValidationError(msg)


def require_credentialed_endpoint(url: str | None, *, field: str) -> None:
    """Refuse to attach a credential with no endpoint to attach it to.

    A credential and no ``api_base`` leaves the driver to route from its
    own defaults, which for several providers is a cleartext localhost
    guess. The credential then goes wherever that guess lands, which is
    neither the operator's choice nor necessarily their machine.

    Args:
        url: The configured endpoint, which must be present here.
        field: What the endpoint is, for the operator-facing message.

    Raises:
        ProviderValidationError: When *url* is absent.
    """
    if url is not None:
        return
    msg = (
        f"{field} resolved a credential but no endpoint to send it to, so "
        f"the driver would route it by its own default. Configure the "
        f"provider's base_url."
    )
    raise ProviderValidationError(msg)


__all__ = [
    "is_confidential_transport",
    "require_confidential_transport",
    "require_credentialed_endpoint",
]
