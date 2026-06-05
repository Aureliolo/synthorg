"""The MCP capability-gap envelope feeds the toolsmith gap store.

Seam A of the autonomous detection loop: when a toolsmith gap sink
is installed, every ``capability_gap`` envelope a handler returns records an
observation, so a repeated gap becomes detectable via ``recurring``. Without
an installed sink the envelope helper is an inert no-op.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.meta.mcp.handlers.common import _PENDING_GAP_TASKS, capability_gap
from synthorg.meta.mcp.server import install_capability_gap_sink, reset_singletons
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore

pytestmark = pytest.mark.unit

_SIG = "synthorg_widget_frobnicate"
_THRESHOLD = 3
_BUFFER = 64


async def test_capability_gap_envelope_feeds_recurring_detection() -> None:
    store = RingBufferCapabilityGapStore(max_observations=_BUFFER)
    install_capability_gap_sink(store)
    try:
        for _ in range(_THRESHOLD):
            capability_gap(_SIG, "no frobnicate primitive yet")
        # Drain the fire-and-forget record_gap tasks scheduled on this loop.
        await asyncio.gather(*list(_PENDING_GAP_TASKS))
        recurring = await store.recurring(
            threshold=_THRESHOLD,
            window=timedelta(hours=1),
            now=datetime.now(UTC) + timedelta(seconds=1),
        )
    finally:
        reset_singletons()

    assert any(gap.signature == _SIG for gap in recurring)


async def test_capability_gap_is_noop_without_installed_sink() -> None:
    reset_singletons()
    before = len(_PENDING_GAP_TASKS)

    result = capability_gap(_SIG, "no sink installed")

    assert "error" in result
    assert len(_PENDING_GAP_TASKS) == before
