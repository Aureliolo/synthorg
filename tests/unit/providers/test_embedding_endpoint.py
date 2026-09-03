"""Tests for resolving where a provider's embedding calls are sent.

Completion dispatch binds a provider's base URL and credential onto every
request; embedding dispatch had no such binding and silently used litellm's
default host. These pin that both now answer from the same configuration.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.providers.drivers.litellm_auth import OPENAI_SDK_ROUTES
from synthorg.providers.embedding_endpoint import (
    EmbeddingEndpoint,
    resolve_embedding_endpoint,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderNotFoundError, ProviderValidationError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit

SDK_ROUTE = next(iter(sorted(OPENAI_SDK_ROUTES)))


#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


def _resolver(**configs: ProviderConfig) -> _Configured:
    """A config resolver holding *configs* keyed by provider name."""
    return mock_of[ConfigResolver](
        get_provider_configs=AsyncMock(return_value=dict(configs)),
    )


def _catalog(**credentials: str) -> _Configured:
    """A credential catalog answering with *credentials*."""
    return mock_of[ConnectionCatalog](
        get_credentials=AsyncMock(return_value=dict(credentials)),
    )


class TestEndpointRouting:
    """The endpoint carries how litellm is to ROUTE the call, not only where.

    Completion dispatch reaches litellm through the provider's declared
    ``litellm_provider`` and resolves an alias to the configured id; an
    embedding call built from the provider NAME alone reaches neither, so a
    provider named for what it is rather than for litellm's routing key, or
    a model bound by alias, could not be embedded through at all.
    """

    async def test_the_litellm_provider_is_the_route(self) -> None:
        endpoint = await resolve_embedding_endpoint(
            "local-embeddings",
            config_resolver=_resolver(
                **{
                    "local-embeddings": ProviderConfig(
                        driver="scripted",
                        litellm_provider=SDK_ROUTE,
                        auth_type=AuthType.NONE,
                        base_url="http://localhost:11434/v1",
                    )
                }
            ),
            catalog=None,
        )

        assert endpoint.route == SDK_ROUTE

    async def test_a_provider_declaring_no_route_carries_none(self) -> None:
        # ``None`` rather than the provider name, so the one place that
        # builds the model reference decides the fallback.
        endpoint = await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted", auth_type=AuthType.NONE
                    )
                }
            ),
            catalog=None,
        )

        assert endpoint.route is None

    async def test_declared_aliases_resolve_to_the_configured_id(self) -> None:
        endpoint = await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted",
                        auth_type=AuthType.NONE,
                        models=(
                            ProviderModelConfig(
                                id="test-embed-001", alias="example-embedding-001"
                            ),
                        ),
                    )
                }
            ),
            catalog=None,
        )

        assert endpoint.model_ids == {
            "test-embed-001": "test-embed-001",
            "example-embedding-001": "test-embed-001",
        }

    def test_a_bare_endpoint_declares_no_routing(self) -> None:
        endpoint = EmbeddingEndpoint(api_base="http://localhost:11434")

        assert endpoint.route is None
        assert endpoint.model_ids is None


class TestEndpointResolution:
    async def test_the_configured_base_url_is_returned(self) -> None:
        # Cleartext and credential-less on the local network: the shape
        # every self-hosted preset ships, which the transport rule
        # deliberately leaves alone.
        endpoint = await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted",
                        auth_type=AuthType.NONE,
                        base_url="http://localhost:11434",
                    )
                }
            ),
            catalog=None,
        )

        assert endpoint.api_base == "http://localhost:11434"
        assert endpoint.api_key is None

    async def test_a_provider_without_a_base_url_resolves_to_none(self) -> None:
        # A hosted provider legitimately declares none, and litellm's own
        # routing is correct for it.
        endpoint = await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted", auth_type=AuthType.NONE
                    )
                }
            ),
            catalog=None,
        )

        assert endpoint.api_base is None

    async def test_the_catalog_credential_is_resolved(self) -> None:
        endpoint = await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted",
                        auth_type=AuthType.API_KEY,
                        connection_name="conn-test",
                        base_url="https://models.invalid",
                    )
                }
            ),
            catalog=_catalog(api_key="embed-secret"),
        )

        assert endpoint.api_key == "embed-secret"

    async def test_an_unresolvable_credential_fails_closed(self) -> None:
        # An unauthenticated embedding request would leak the text being
        # embedded, so this refuses rather than sending it anonymously.
        from synthorg.providers.errors import AuthenticationError

        with pytest.raises(AuthenticationError):
            _ = await resolve_embedding_endpoint(
                "test-provider",
                config_resolver=_resolver(
                    **{
                        "test-provider": ProviderConfig(
                            driver="scripted",
                            auth_type=AuthType.API_KEY,
                            connection_name="conn-test",
                        )
                    }
                ),
                catalog=_catalog(),
            )

    async def test_an_unconfigured_provider_is_refused(self) -> None:
        with pytest.raises(ProviderNotFoundError):
            _ = await resolve_embedding_endpoint(
                "missing-provider",
                config_resolver=_resolver(),
                catalog=None,
            )

    async def test_a_header_auth_provider_carries_its_headers(self) -> None:
        # Embedding authenticates exactly as completion does, so an auth type
        # that signs with a header has to reach the embedding call too.
        endpoint = await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted",
                        auth_type=AuthType.CUSTOM_HEADER,
                        custom_header_name="X-Test-Auth",
                        custom_header_value="header-secret",
                        base_url="https://models.invalid",
                    )
                }
            ),
            catalog=None,
        )

        assert endpoint.extra_headers == {"X-Test-Auth": "header-secret"}
        assert endpoint.api_key is None


class TestCleartextTransport:
    """An embedding call may cross http only to this machine's own network."""

    async def _resolve(self, base_url: str, **catalog: str) -> EmbeddingEndpoint:
        """Resolve an API-key provider configured at *base_url*."""
        return await resolve_embedding_endpoint(
            "test-provider",
            config_resolver=_resolver(
                **{
                    "test-provider": ProviderConfig(
                        driver="scripted",
                        auth_type=AuthType.API_KEY,
                        connection_name="conn-test",
                        base_url=base_url,
                    )
                }
            ),
            catalog=_catalog(**catalog),
        )

    async def test_a_remote_cleartext_endpoint_refuses_the_credential(self) -> None:
        with pytest.raises(ProviderValidationError):
            _ = await self._resolve(
                "http://models.invalid:11434", api_key="embed-secret"
            )

    async def test_a_remote_cleartext_endpoint_is_refused_uncredentialed(self) -> None:
        # The request body is the text being embedded, which is company
        # memory, so a provider that needs no credential still may not send
        # it across the open internet in the clear.
        with pytest.raises(ProviderValidationError):
            _ = await resolve_embedding_endpoint(
                "test-provider",
                config_resolver=_resolver(
                    **{
                        "test-provider": ProviderConfig(
                            driver="scripted",
                            auth_type=AuthType.NONE,
                            base_url="http://models.invalid:11434",
                        )
                    }
                ),
                catalog=None,
            )

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://[::1]:11434",
            "http://172.17.0.1:11434",
            "http://192.168.1.50:8000",
            "http://host.docker.internal:1234/v1",
        ],
    )
    async def test_a_local_cleartext_endpoint_still_carries_it(
        self, base_url: str
    ) -> None:
        # Self-hosting is the reason base_url exists, and the shipped
        # presets address exactly these; refusing them would make an
        # authenticated local inference server unreachable.
        endpoint = await self._resolve(base_url, api_key="embed-secret")

        assert endpoint.api_key == "embed-secret"

    async def test_a_header_credential_is_judged_the_same_way(self) -> None:
        # The header carries the secret just as the bearer key does, so the
        # transport rule cannot key on ``api_key`` alone.
        with pytest.raises(ProviderValidationError):
            _ = await resolve_embedding_endpoint(
                "test-provider",
                config_resolver=_resolver(
                    **{
                        "test-provider": ProviderConfig(
                            driver="scripted",
                            auth_type=AuthType.CUSTOM_HEADER,
                            custom_header_name="X-Test-Auth",
                            custom_header_value="header-secret",
                            base_url="http://models.invalid:11434",
                        )
                    }
                ),
                catalog=None,
            )
