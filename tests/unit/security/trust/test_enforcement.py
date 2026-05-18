"""Tests for trust-driven tool-permission narrowing."""

import pytest

from synthorg.core.agent import ToolPermissions
from synthorg.core.enums import ToolAccessLevel
from synthorg.security.trust.enforcement import (
    resolve_effective_tool_permissions,
)

pytestmark = pytest.mark.unit


class TestResolveEffectiveToolPermissions:
    """The more restrictive of identity vs trust level wins."""

    def test_trust_below_identity_narrows(self) -> None:
        tools = ToolPermissions(access_level=ToolAccessLevel.STANDARD)
        effective, narrowed = resolve_effective_tool_permissions(
            tools,
            ToolAccessLevel.SANDBOXED,
        )
        assert narrowed is True
        assert effective.access_level == ToolAccessLevel.SANDBOXED

    def test_trust_above_identity_does_not_grant(self) -> None:
        tools = ToolPermissions(access_level=ToolAccessLevel.STANDARD)
        effective, narrowed = resolve_effective_tool_permissions(
            tools,
            ToolAccessLevel.ELEVATED,
        )
        assert narrowed is False
        assert effective is tools

    def test_equal_levels_pass_through(self) -> None:
        tools = ToolPermissions(access_level=ToolAccessLevel.STANDARD)
        effective, narrowed = resolve_effective_tool_permissions(
            tools,
            ToolAccessLevel.STANDARD,
        )
        assert narrowed is False
        assert effective is tools

    def test_identity_custom_untouched(self) -> None:
        tools = ToolPermissions(access_level=ToolAccessLevel.CUSTOM)
        effective, narrowed = resolve_effective_tool_permissions(
            tools,
            ToolAccessLevel.SANDBOXED,
        )
        assert narrowed is False
        assert effective is tools

    def test_trust_custom_untouched(self) -> None:
        tools = ToolPermissions(access_level=ToolAccessLevel.ELEVATED)
        effective, narrowed = resolve_effective_tool_permissions(
            tools,
            ToolAccessLevel.CUSTOM,
        )
        assert narrowed is False
        assert effective is tools

    def test_explicit_lists_preserved_on_narrow(self) -> None:
        tools = ToolPermissions(
            access_level=ToolAccessLevel.ELEVATED,
            allowed=("special_tool",),
            denied=("blocked_tool",),
        )
        effective, narrowed = resolve_effective_tool_permissions(
            tools,
            ToolAccessLevel.RESTRICTED,
        )
        assert narrowed is True
        assert effective.access_level == ToolAccessLevel.RESTRICTED
        assert effective.allowed == ("special_tool",)
        assert effective.denied == ("blocked_tool",)
