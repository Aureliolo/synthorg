"""Coverage for the api.sse_keepalive_seconds resolution chain.

The keepalive interval used by the AG-UI SSE stream resolves through
``ConfigResolver.get_float`` so an operator can tune the cadence
without restarting the process.  When :class:`AppState` has no
resolver wired (test harness, anonymous boot), the helper falls back
to ``_SSE_KEEPALIVE_FALLBACK_SECONDS`` -- the same value as the
registered registry default.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers import events as events_mod
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


async def test_falls_back_when_app_state_is_none() -> None:
    result = await events_mod._resolve_sse_keepalive_seconds(None)
    assert result == events_mod._SSE_KEEPALIVE_FALLBACK_SECONDS


async def test_falls_back_when_resolver_unwired() -> None:
    state = make_app_state()
    result = await events_mod._resolve_sse_keepalive_seconds(state)
    assert result == events_mod._SSE_KEEPALIVE_FALLBACK_SECONDS


async def test_uses_resolver_value_when_wired() -> None:
    resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=12.5))
    state = make_app_state(config_resolver=resolver)
    result = await events_mod._resolve_sse_keepalive_seconds(state)
    assert result == 12.5
    resolver.get_float.assert_awaited_once_with("api", "sse_keepalive_seconds")


async def test_resolver_outage_falls_back() -> None:
    resolver = mock_of[ConfigResolver](
        get_float=AsyncMock(side_effect=RuntimeError("transient")),
    )
    state = make_app_state(config_resolver=resolver)
    result = await events_mod._resolve_sse_keepalive_seconds(state)
    assert result == events_mod._SSE_KEEPALIVE_FALLBACK_SECONDS
