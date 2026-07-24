"""Tests for the on-startup wiring of the inbound Socket-Mode consumer."""

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.chat_inbound_wiring import (
    start_chat_inbound_consumer,
)
from synthorg.api.state import AppState
from synthorg.integrations.chat_api.inbound.consumer import ChatInboundConsumer
from synthorg.integrations.chat_api.inbound.registry import InboundThreadRegistry
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


def _app_state(
    *,
    catalog: object,
    registry: object,
    resolver: object,
) -> AppState:
    integrations = SimpleNamespace(
        connection_catalog=catalog, inbound_thread_registry=registry
    )
    settings = SimpleNamespace(config_resolver=resolver)

    def _slice(slice_type: type) -> object:
        if slice_type is IntegrationsStateSlice:
            return integrations
        if slice_type is SettingsStateSlice:
            return settings
        raise KeyError(slice_type)

    state: AppState = mock_of[AppState](slice=_slice)
    return state


def _resolver() -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_bool=AsyncMock(spec=ConfigResolver.get_bool, return_value=False),
        get_str=AsyncMock(spec=ConfigResolver.get_str, return_value=""),
    )
    return resolver


def _catalog() -> ConnectionCatalog:
    catalog: ConnectionCatalog = mock_of[ConnectionCatalog](
        get_credentials=AsyncMock(
            spec=ConnectionCatalog.get_credentials, return_value={}
        ),
    )
    return catalog


class TestStartChatInboundConsumer:
    @pytest.mark.parametrize(
        "make_state",
        [
            lambda: _app_state(
                catalog=None, registry=InboundThreadRegistry(), resolver=_resolver()
            ),
            lambda: _app_state(catalog=_catalog(), registry=None, resolver=_resolver()),
        ],
        ids=["no_catalog", "no_registry"],
    )
    async def test_returns_none_when_collaborators_missing(
        self, make_state: Callable[[], AppState]
    ) -> None:
        assert await start_chat_inbound_consumer(make_state()) is None

    async def test_returns_none_when_start_fails(self) -> None:
        # An unwired config resolver makes config_resolver_of raise; the
        # best-effort wiring logs and returns None rather than aborting boot.
        state = _app_state(
            catalog=_catalog(), registry=InboundThreadRegistry(), resolver=None
        )
        assert await start_chat_inbound_consumer(state) is None

    async def test_builds_and_starts_consumer(self) -> None:
        state = _app_state(
            catalog=_catalog(),
            registry=InboundThreadRegistry(),
            resolver=_resolver(),
        )
        consumer = await start_chat_inbound_consumer(state)
        assert isinstance(consumer, ChatInboundConsumer)
        # The resident loop was started; stop it to clean up.
        await consumer.stop()
