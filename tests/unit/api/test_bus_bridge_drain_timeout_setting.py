"""Coverage for the communication.bus_bridge_drain_timeout_seconds chain.

The bridge's stop() drain deadline resolves through
``ConfigResolver.get_float`` so an operator can tune the hard
shutdown ceiling without restarting the process.  When no resolver
is wired or the resolver lookup fails, the helper falls back to
``_STOP_DRAIN_TIMEOUT_SECONDS`` (the registered default).  The
fallback log fires once per failure run and clears on recovery so a
prolonged settings outage cannot flood logs.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.bus_bridge import _STOP_DRAIN_TIMEOUT_SECONDS, MessageBusBridge

pytestmark = pytest.mark.unit


def _make_bridge(resolver: Any | None) -> MessageBusBridge:
    bus = MagicMock()
    plugin = MagicMock()
    return MessageBusBridge(bus, plugin, config_resolver=resolver)


async def test_falls_back_when_no_resolver() -> None:
    bridge = _make_bridge(None)
    timeout = await bridge._get_stop_drain_timeout()
    assert timeout == _STOP_DRAIN_TIMEOUT_SECONDS


async def test_uses_resolver_value_when_wired() -> None:
    resolver = AsyncMock()
    resolver.get_float = AsyncMock(return_value=42.5)
    bridge = _make_bridge(resolver)
    timeout = await bridge._get_stop_drain_timeout()
    assert timeout == 42.5
    resolver.get_float.assert_awaited_once_with(
        "communication", "bus_bridge_drain_timeout_seconds"
    )


async def test_resolver_outage_falls_back_and_log_once() -> None:
    resolver = AsyncMock()
    resolver.get_float = AsyncMock(side_effect=RuntimeError("transient"))
    bridge = _make_bridge(resolver)

    # Three consecutive failures: the warning suppression gate latches
    # after the first emission, so the in-process flag should flip True
    # exactly once.
    assert bridge._drain_timeout_fallback_logged is False
    timeout = await bridge._get_stop_drain_timeout()
    assert timeout == _STOP_DRAIN_TIMEOUT_SECONDS
    assert bridge._drain_timeout_fallback_logged is True
    timeout = await bridge._get_stop_drain_timeout()
    assert timeout == _STOP_DRAIN_TIMEOUT_SECONDS
    assert bridge._drain_timeout_fallback_logged is True

    # Recovery clears the flag so the next failure-run can log again.
    resolver.get_float = AsyncMock(return_value=15.0)
    timeout = await bridge._get_stop_drain_timeout()
    assert timeout == 15.0
    assert bridge._drain_timeout_fallback_logged is False
