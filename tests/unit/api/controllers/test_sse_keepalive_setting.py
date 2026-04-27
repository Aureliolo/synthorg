"""Coverage for the api.sse_keepalive_seconds resolution chain.

The keepalive interval used by the AG-UI SSE stream resolves through
``ConfigResolver.get_float`` so an operator can tune the cadence
without restarting the process.  When :class:`AppState` has no
resolver wired (test harness, anonymous boot), the helper falls back
to ``_SSE_KEEPALIVE_FALLBACK_SECONDS`` -- the same value as the
registered registry default.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers import events as events_mod

pytestmark = pytest.mark.unit


class _AppStateNoResolver:
    has_config_resolver = False


class _AppStateWithResolver:
    def __init__(self, value: float) -> None:
        self.has_config_resolver = True
        self.config_resolver = AsyncMock()
        self.config_resolver.get_float = AsyncMock(return_value=value)


async def test_falls_back_when_app_state_is_none() -> None:
    result = await events_mod._resolve_sse_keepalive_seconds(None)
    assert result == events_mod._SSE_KEEPALIVE_FALLBACK_SECONDS


async def test_falls_back_when_resolver_unwired() -> None:
    state: Any = _AppStateNoResolver()
    result = await events_mod._resolve_sse_keepalive_seconds(state)
    assert result == events_mod._SSE_KEEPALIVE_FALLBACK_SECONDS


async def test_uses_resolver_value_when_wired() -> None:
    state: Any = _AppStateWithResolver(value=12.5)
    result = await events_mod._resolve_sse_keepalive_seconds(state)
    assert result == 12.5
    state.config_resolver.get_float.assert_awaited_once_with(
        "api", "sse_keepalive_seconds"
    )


async def test_resolver_outage_falls_back() -> None:
    # ``value`` is irrelevant here because ``get_float`` is replaced
    # with a side_effect that raises before returning; pass any
    # placeholder.  Use a clearly arbitrary sentinel so a future
    # reader does not assume the value matters.
    state: Any = _AppStateWithResolver(value=-1.0)
    state.config_resolver.get_float.side_effect = RuntimeError("transient")
    result = await events_mod._resolve_sse_keepalive_seconds(state)
    assert result == events_mod._SSE_KEEPALIVE_FALLBACK_SECONDS
