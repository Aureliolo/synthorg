"""Regression test: MCP handler capability check on a real ``AppState``.

Production bug #2 of the slice migration: handlers read a deleted
``getattr(app_state, "has_<service>", False)`` (always falsy on the thin
``AppState``) and so degraded to ``capability_gap`` unconditionally. The
fix reads the slice field. These tests run on a REAL ``make_app_state``
(not a ``SimpleNamespace`` that would satisfy both the old has_X attr and
the new slice) so a reintroduced getattr-has_X read is actually caught:
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
    # The discriminating assertion for bug #2: with the service wired the
    # handler must NOT degrade to capability_gap. The buggy has_X read
    # gapped unconditionally, so only the wired-case path catches it.
    service = mock_of[SelfImprovementService](get_config=lambda: {"enabled": True})
    app_state = make_app_state(
        slices={MetaStateSlice: {"self_improvement_service": service}}
    )
    result = await _meta_get_config(app_state=app_state, arguments={})
    assert "not_supported" not in result
    assert "enabled" in result
