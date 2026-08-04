"""Tests for resolving where a provider's embedding calls are sent.

Completion dispatch binds a provider's base URL and credential onto every
request; embedding dispatch had no such binding and silently used litellm's
default host. These pin that both now answer from the same configuration.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.config.provider_schema import ProviderConfig
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.providers.embedding_endpoint import resolve_embedding_endpoint
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


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


class TestEndpointResolution:
    async def test_the_configured_base_url_is_returned(self) -> None:
        endpoint = await resolve_embedding_endpoint(
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

        assert endpoint.api_base == "http://models.invalid:11434"
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
                        base_url="http://models.invalid:11434",
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
