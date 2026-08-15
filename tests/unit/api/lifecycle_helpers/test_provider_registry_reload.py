"""Tests for the boot-time reload of the persisted provider registry.

The distinction under test is the one that shipped missing: a deployment
with no providers configured and a deployment whose providers could not be
read reached the same ``None`` return, so an operator with a full provider
set was told they had an empty company.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.provider_registry_reload import (
    reload_persisted_provider_registry,
)
from synthorg.api.state import AppState
from synthorg.config.provider_configs_read import (
    ProviderConfigsRead,
    ProviderConfigsStatus,
    RejectedProviderConfig,
)
from synthorg.config.provider_schema import ProviderConfig
from synthorg.providers.errors import ProviderConfigUnreadableError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(read: ProviderConfigsRead) -> AppState:
    """Return app state whose resolver reports *read* as the persisted config."""
    return make_app_state(
        config_resolver=mock_of[ConfigResolver](
            get_provider_configs_read=AsyncMock(
                spec=ConfigResolver.get_provider_configs_read,
                return_value=read,
            ),
            get_int=AsyncMock(spec=ConfigResolver.get_int, return_value=None),
        )
    )


async def test_unreadable_config_raises_rather_than_reading_as_empty() -> None:
    app_state = _app_state(
        ProviderConfigsRead(
            status=ProviderConfigsStatus.UNREADABLE,
            providers={},
            rejected=(
                RejectedProviderConfig(name="alpha", reason="ValidationError: no"),
            ),
        )
    )

    with pytest.raises(ProviderConfigUnreadableError):
        await reload_persisted_provider_registry(app_state)


async def test_no_persisted_providers_is_still_a_first_run() -> None:
    app_state = _app_state(
        ProviderConfigsRead(status=ProviderConfigsStatus.OK, providers={})
    )

    assert await reload_persisted_provider_registry(app_state) is None


async def test_partial_read_registers_the_providers_that_survived() -> None:
    """One rejected entry costs that entry; the rest of the org keeps running."""
    app_state = _app_state(
        ProviderConfigsRead(
            status=ProviderConfigsStatus.PARTIAL,
            providers={"alpha": ProviderConfig(connection_name="conn-a")},
            rejected=(
                RejectedProviderConfig(name="beta", reason="ValidationError: no"),
            ),
        )
    )

    registry = await reload_persisted_provider_registry(app_state)

    assert registry is not None
    assert sorted(registry.list_providers()) == ["alpha"]


async def test_unwired_resolver_reloads_nothing() -> None:
    assert await reload_persisted_provider_registry(make_app_state()) is None
