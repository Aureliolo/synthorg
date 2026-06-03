"""Regression test: MCP handler capability check on a real ``AppState``.

MCP handlers must read capability state from the slice field, not a
``getattr(app_state, "has_<service>", False)`` fallback (always falsy on
the thin ``AppState``, which would degrade to ``capability_gap``
unconditionally). These tests run on a REAL ``make_app_state`` (not a
``SimpleNamespace`` that would satisfy both a ``has_X`` attr and the
slice) so a reintroduced ``getattr``-``has_X`` read is actually caught:
with the service wired the handler must reach the live path, and without
it must report the gap.
"""

import pytest

from synthorg.meta.mcp.handlers.meta import _meta_get_config
from synthorg.meta.service import SelfImprovementService
from synthorg.meta.state import MetaStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


async def test_capability_gap_when_self_improvement_unwired() -> None:
    app_state = make_app_state()
    result = await _meta_get_config(app_state=app_state, arguments={})
    assert "not_supported" in result


async def test_live_path_when_self_improvement_wired() -> None:
    # Discriminating assertion: with the service wired the handler must
    # NOT degrade to capability_gap. A getattr-has_X read would gap
    # unconditionally, so only the wired-case path catches it.
    service = mock_of[SelfImprovementService](get_config=lambda: {"enabled": True})
    app_state = make_app_state(
        slices={MetaStateSlice: {"self_improvement_service": service}}
    )
    result = await _meta_get_config(app_state=app_state, arguments={})
    assert "not_supported" not in result
    assert "enabled" in result
