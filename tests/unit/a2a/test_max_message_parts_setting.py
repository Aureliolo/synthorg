"""Coverage for the a2a.max_message_parts resolution chain.

The maximum message-parts ceiling resolves through
``ConfigResolver.get_int`` so an operator can tune the cap without
restarting the gateway.  When :class:`AppState` has no resolver wired
or the resolver lookup fails, the helper falls back to
``_MAX_MESSAGE_PARTS_FALLBACK`` (the registered registry default) so
a transient settings outage cannot let an oversized message slip
through.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.a2a import gateway as gateway_mod

pytestmark = pytest.mark.unit


class _AppStateNoResolver:
    has_config_resolver = False


class _AppStateWithResolver:
    def __init__(self, value: int) -> None:
        self.has_config_resolver = True
        self.config_resolver = AsyncMock()
        self.config_resolver.get_int = AsyncMock(return_value=value)


async def test_falls_back_when_app_state_is_none() -> None:
    result = await gateway_mod._resolve_max_message_parts(None)
    assert result == gateway_mod._MAX_MESSAGE_PARTS_FALLBACK


async def test_falls_back_when_resolver_unwired() -> None:
    state: Any = _AppStateNoResolver()
    result = await gateway_mod._resolve_max_message_parts(state)
    assert result == gateway_mod._MAX_MESSAGE_PARTS_FALLBACK


async def test_uses_resolver_value_when_wired() -> None:
    state: Any = _AppStateWithResolver(value=42)
    result = await gateway_mod._resolve_max_message_parts(state)
    assert result == 42
    state.config_resolver.get_int.assert_awaited_once_with("a2a", "max_message_parts")


async def test_resolver_outage_falls_back() -> None:
    # ``value`` is irrelevant here because ``get_int`` is replaced
    # with a side_effect that raises before returning; pass any
    # placeholder.  Use a clearly arbitrary sentinel so a future
    # reader does not assume the value matters.
    state: Any = _AppStateWithResolver(value=-1)
    state.config_resolver.get_int.side_effect = RuntimeError("transient")
    result = await gateway_mod._resolve_max_message_parts(state)
    assert result == gateway_mod._MAX_MESSAGE_PARTS_FALLBACK
