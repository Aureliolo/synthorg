"""Container-registry API client factory keyed on the provider preset.

Selects the per-provider client via a
:class:`~synthorg.core.registry.StrategyRegistry`, keyed on the registry
connection's declared provider, so the agent-facing publish tools stay
vendor-neutral. Every OCI-compliant registry maps to the one generic client;
a registry whose auth genuinely differs (ECR SigV4, GCR OAuth) is added as a
new preset plus client, and the tool layer does not change.

There is no per-provider host allowlist: a registry's API host is genuinely
operator-chosen (self-hosted registries are normal). The egress guarantee
instead comes from pinning the client to that one ``base_url`` and defining
every path in code, so a call can never leave the host the operator approved.
"""

from synthorg.core.registry import StrategyRegistry
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.registry_target import RegistryProvider
from synthorg.integrations.errors import RegistryApiError
from synthorg.integrations.registry_api.oci import build_oci_client
from synthorg.integrations.registry_api.protocol import RegistryApiClient
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import REGISTRY_API_CONFIG_INVALID

logger = get_logger(__name__)

_HTTPS_PREFIX: str = "https://"


def _require_base_url(base_url: str) -> str:
    """Reject an empty or non-HTTPS registry base URL.

    Args:
        base_url: The connection's configured base URL.

    Returns:
        The validated base URL.

    Raises:
        RegistryApiError: When it is blank or not HTTPS. Plain HTTP would put
            the registry credential on the wire in clear text, so it is
            refused rather than downgraded.
    """
    if not base_url or not base_url.startswith(_HTTPS_PREFIX):
        # Never log the value: a base_url can carry embedded credentials.
        logger.warning(REGISTRY_API_CONFIG_INVALID, reason="base_url_not_https")
        msg = "Registry connection base_url must be an https:// URL"
        raise RegistryApiError(msg)
    return base_url


def _build_oci(
    *,
    base_url: str,
    repository: NotBlankStr,
    username: str,
    token: str,
    timeout: float,
    auth_host: str,
) -> RegistryApiClient:
    return build_oci_client(
        base_url=_require_base_url(base_url),
        repository=repository,
        username=username,
        token=token,
        timeout=timeout,
        auth_host=auth_host,
    )


_REGISTRY: StrategyRegistry[RegistryApiClient] = StrategyRegistry(
    {RegistryProvider.GENERIC_OCI: _build_oci},
    kind="registry_api_client",
)


def registry_api_supported(provider: RegistryProvider) -> bool:
    """Return whether a registry client is wired for the provider.

    Returns:
        ``True`` when a client is wired.
    """
    return provider in _REGISTRY


def build_registry_api_client(
    *,
    provider: RegistryProvider,
    base_url: str,
    repository: NotBlankStr,
    username: str,
    token: str,
    timeout: float,
    auth_host: str,
) -> RegistryApiClient:
    """Build the per-provider registry API client.

    Args:
        provider: Selects the provider implementation.
        base_url: The connection's base URL; every request is pinned to it.
        repository: The operator-configured repository the client is bound to.
        username: The Basic-auth principal for the token exchange (may be empty).
        token: Resolved registry credential (header auth only, never logged).
        timeout: Per-request timeout in seconds.
        auth_host: Operator-declared token-exchange host, if the registry
            authenticates on a host other than its own.

    Returns:
        A client satisfying :class:`RegistryApiClient`.

    Raises:
        StrategyFactoryNotFoundError: ``provider`` has no wired client
            (check ``registry_api_supported`` first).
        RegistryApiError: The base URL is blank or not HTTPS.
    """
    return _REGISTRY.build(
        provider,
        base_url=base_url,
        repository=repository,
        username=username,
        token=token,
        timeout=timeout,
        auth_host=auth_host,
    )


__all__ = ["build_registry_api_client", "registry_api_supported"]
