# module-kind: code
"""Where an embedding call for a named provider is actually sent.

Completion dispatch binds a provider's configured ``base_url`` and
credential onto every request. Embedding dispatch has the same need and no
reason to answer it differently: a model reference alone leaves litellm to
guess a default host, which for a self-hosted provider is the wrong machine
and cannot be corrected by any amount of configuration.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_NOT_FOUND
from synthorg.providers.drivers.litellm_auth import AuthContext, resolve_auth_material
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


@dataclass(frozen=True)
class EmbeddingEndpoint:
    """The transport half of an embedding binding.

    Attributes:
        api_base: Base URL the provider is configured at; ``None`` leaves
            litellm's own routing in charge, which is correct only for a
            hosted provider that declares no base URL.
        api_key: Resolved credential, when the auth type carries one.
        extra_headers: Custom auth headers, when the auth type uses them.
            A read-only view, matching :class:`AuthMaterial`: the frozen
            dataclass protects the field, not the mapping it points at.
    """

    api_base: str | None = None
    api_key: str | None = None
    extra_headers: Mapping[str, str] | None = None


async def resolve_embedding_endpoint(
    provider: str,
    *,
    config_resolver: ConfigResolver,
    catalog: ConnectionCatalog | None,
) -> EmbeddingEndpoint:
    """Resolve where *provider*'s embedding calls go, and how they authenticate.

    Args:
        provider: Name of the configured provider that hosts the model.
        config_resolver: Source of the persisted provider configs.
        catalog: Source of connection credentials; ``None`` is the
            catalog-less degraded path, which omits the credential.

    Returns:
        The endpoint an embedding call should be addressed to.

    Raises:
        ProviderNotFoundError: If *provider* is not configured, which is a
            binding the operator has to fix rather than one to guess past.
        AuthenticationError: If a wired catalog did not resolve a credential
            the provider's auth type requires.
    """
    configs = await config_resolver.get_provider_configs()
    config = configs.get(provider)
    if config is None:
        msg = (
            f"Embedding provider {provider!r} is not configured, so there is "
            f"no endpoint to send its embedding calls to"
        )
        logger.warning(PROVIDER_NOT_FOUND, provider=provider, usage="embedding")
        raise ProviderNotFoundError(msg)
    resolved = (
        await catalog.get_credentials(config.connection_name)
        if catalog is not None and config.connection_name is not None
        else None
    )
    material = resolve_auth_material(
        AuthContext(
            config=config,
            resolved=resolved,
            catalog_present=catalog is not None,
            provider_name=provider,
            litellm_model=provider,
        )
    )
    return EmbeddingEndpoint(
        api_base=config.base_url,
        api_key=material.api_key,
        extra_headers=material.extra_headers,
    )


__all__ = ["EmbeddingEndpoint", "resolve_embedding_endpoint"]
