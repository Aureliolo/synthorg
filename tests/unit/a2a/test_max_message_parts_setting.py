"""Coverage for the a2a.max_message_parts resolution chain.

The maximum message-parts ceiling resolves through
``ConfigResolver.get_int`` so an operator can tune the cap without
restarting the gateway.  When :class:`AppState` has no resolver wired
or the resolver lookup fails, the helper falls back to
``_MAX_MESSAGE_PARTS_FALLBACK`` (the registered registry default) so
a transient settings outage cannot let an oversized message slip
through.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.a2a import gateway as gateway_mod
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


async def test_falls_back_when_app_state_is_none() -> None:
    result = await gateway_mod._resolve_max_message_parts(None)
    assert result == gateway_mod._MAX_MESSAGE_PARTS_FALLBACK


async def test_falls_back_when_resolver_unwired() -> None:
    state = make_app_state()
    result = await gateway_mod._resolve_max_message_parts(state)
    assert result == gateway_mod._MAX_MESSAGE_PARTS_FALLBACK


async def test_uses_resolver_value_when_wired() -> None:
    resolver = mock_of[ConfigResolver](get_int=AsyncMock(return_value=42))
    state = make_app_state(config_resolver=resolver)
    result = await gateway_mod._resolve_max_message_parts(state)
    assert result == 42
    resolver.get_int.assert_awaited_once_with("a2a", "max_message_parts")


async def test_resolver_outage_falls_back() -> None:
    resolver = mock_of[ConfigResolver](
        get_int=AsyncMock(side_effect=RuntimeError("transient")),
    )
    state = make_app_state(config_resolver=resolver)
    result = await gateway_mod._resolve_max_message_parts(state)
    assert result == gateway_mod._MAX_MESSAGE_PARTS_FALLBACK
