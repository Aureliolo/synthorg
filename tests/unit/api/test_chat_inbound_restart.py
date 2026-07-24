"""The inbound consumer restart drops its handle only on a clean stop.

A stop that times out leaves the prior loop task still owning the Socket-Mode
session. Returning a fresh handle would let the next lifespan entry open a
second session against the same connection, so the prior handle is returned
unchanged and the restart skipped until a later entry stops it cleanly.
"""

from collections.abc import Awaitable
from unittest.mock import AsyncMock

import pytest

import synthorg.api.lifecycle_helpers.chat_inbound_wiring as wiring_mod
from synthorg.api.lifecycle_helpers.chat_inbound_wiring import (
    restart_chat_inbound_consumer,
)
from synthorg.api.state import AppState
from synthorg.integrations.chat_api.inbound.consumer import ChatInboundConsumer
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


def _consumer() -> ChatInboundConsumer:
    """A typed consumer double whose ``stop()`` is an awaitable no-op."""
    consumer: ChatInboundConsumer = mock_of[ChatInboundConsumer](
        stop=AsyncMock(spec=ChatInboundConsumer.stop, return_value=None),
    )
    return consumer


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop_ok: bool,
    fresh: ChatInboundConsumer,
) -> None:
    async def _fake_try_stop(coro: Awaitable[None], *_a: object, **_k: object) -> bool:
        await coro  # drain the stop() coroutine so no warning is raised
        return stop_ok

    async def _fake_start(_app_state: object) -> ChatInboundConsumer:
        return fresh

    monkeypatch.setattr(wiring_mod, "_try_stop", _fake_try_stop)
    monkeypatch.setattr(wiring_mod, "start_chat_inbound_consumer", _fake_start)


class TestRestartChatInboundConsumer:
    async def test_starts_fresh_when_no_prior_consumer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = _consumer()
        _patch(monkeypatch, stop_ok=True, fresh=fresh)

        result = await restart_chat_inbound_consumer(mock_of[AppState](), None)

        assert result is fresh

    async def test_replaces_prior_after_a_clean_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = _consumer()
        _patch(monkeypatch, stop_ok=True, fresh=fresh)

        result = await restart_chat_inbound_consumer(mock_of[AppState](), _consumer())

        assert result is fresh

    async def test_retains_prior_handle_when_stop_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = _consumer()
        _patch(monkeypatch, stop_ok=False, fresh=fresh)
        prior = _consumer()

        result = await restart_chat_inbound_consumer(mock_of[AppState](), prior)

        # The prior handle is returned (not the fresh one, not None) so the
        # next lifespan entry re-attempts the stop instead of opening a second
        # session against the same connection.
        assert result is prior
