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

from synthorg.config.provider_schema import ProviderConfig
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_NOT_FOUND
from synthorg.providers.drivers.litellm_auth import (
    AuthContext,
    AuthMaterial,
    resolve_auth_material,
)
from synthorg.providers.drivers.litellm_model_catalog import build_model_lookup
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.providers.transport_policy import (
    require_confidential_transport,
    require_credentialed_endpoint,
)
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
        route: The provider's declared ``litellm_provider``, which is the
            key litellm routes on. A provider is named for what it is
            (``local-embeddings``), and litellm knows only its own keys, so
            a call built on the name alone reaches nothing self-hosted.
            ``None`` leaves the name as the route, which is what completion
            dispatch does too.
        model_ids: Every alias and id the provider declares, mapped to the
            configured id, so a model bound by alias reaches litellm under
            the name the endpoint actually serves. ``None`` declares no
            aliases, and the id is sent as written.
    """

    api_base: str | None = None
    api_key: str | None = None
    extra_headers: Mapping[str, str] | None = None
    route: str | None = None
    model_ids: Mapping[str, str] | None = None


def endpoint_for_config(
    provider: str, config: ProviderConfig, *, material: AuthMaterial
) -> EmbeddingEndpoint:
    """Bind one provider config into the endpoint its embedding calls use.

    The single owner of the shape: the boot-time resolver below reads the
    config off the persisted settings, and a harness preflight reads it off
    a file, and both must reach litellm the same way or the smoke measures a
    route the deployment never takes.

    Args:
        provider: The provider's configured name, for the refusal message.
        config: The provider's own config.
        material: Its resolved auth material.

    Returns:
        The endpoint.

    Raises:
        ProviderValidationError: If the endpoint would be addressed in
            cleartext beyond this machine's own network, or a credential
            resolved with no endpoint to send it to.
    """
    # Checked for every provider, not only credentialed ones: the request
    # body is the text being embedded, which is company memory, so an
    # AuthType.NONE provider at a public http:// endpoint leaks the thing
    # the credential was only protecting access to.
    field = f"Embedding provider {provider!r}"
    require_confidential_transport(config.base_url, field=field)
    if material.api_key is not None or material.extra_headers:
        require_credentialed_endpoint(config.base_url, field=field)
    return EmbeddingEndpoint(
        api_base=config.base_url,
        api_key=material.api_key,
        extra_headers=material.extra_headers,
        route=config.litellm_provider,
        model_ids={
            key: model.id for key, model in build_model_lookup(config.models).items()
        },
    )


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
        ProviderValidationError: If the endpoint would be addressed in
            cleartext beyond this machine's own network, or a credential
            resolved with no endpoint to send it to.
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
    return endpoint_for_config(provider, config, material=material)


__all__ = ["EmbeddingEndpoint", "resolve_embedding_endpoint"]
