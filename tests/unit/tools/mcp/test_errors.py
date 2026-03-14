"""Tests for the MCP error hierarchy."""

import pytest

from synthorg.tools.errors import ToolError
from synthorg.tools.mcp.errors import (
    MCPConnectionError,
    MCPDiscoveryError,
    MCPError,
    MCPInvocationError,
    MCPTimeoutError,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestMCPErrorHierarchy:
    """All MCP errors extend MCPError which extends ToolError."""

    @pytest.mark.parametrize(
        "error_cls",
        [
            MCPError,
            MCPConnectionError,
            MCPTimeoutError,
            MCPDiscoveryError,
            MCPInvocationError,
        ],
    )
    def test_isinstance_tool_error(
        self,
        error_cls: type[MCPError],
    ) -> None:
        err = error_cls("test message")
        assert isinstance(err, ToolError)
        assert isinstance(err, MCPError)

    @pytest.mark.parametrize(
        "error_cls",
        [
            MCPConnectionError,
            MCPTimeoutError,
            MCPDiscoveryError,
            MCPInvocationError,
        ],
    )
    def test_isinstance_mcp_error(
        self,
        error_cls: type[MCPError],
    ) -> None:
        err = error_cls("test")
        assert isinstance(err, MCPError)


class TestMCPErrorContext:
    """Context propagation through the error hierarchy."""

    def test_context_propagated(self) -> None:
        ctx = {"server": "test", "tool": "foo"}
        err = MCPInvocationError("failed", context=ctx)
        assert err.context["server"] == "test"
        assert err.context["tool"] == "foo"

    def test_context_defaults_empty(self) -> None:
        err = MCPConnectionError("conn failed")
        assert len(err.context) == 0

    def test_context_immutable(self) -> None:
        err = MCPTimeoutError(
            "timed out",
            context={"key": "val"},
        )
        with pytest.raises(TypeError):
            err.context["new_key"] = "new_val"  # type: ignore[index]

    def test_message_attribute(self) -> None:
        err = MCPDiscoveryError("discovery failed")
        assert err.message == "discovery failed"
        assert str(err) == "discovery failed"

    def test_str_with_context(self) -> None:
        err = MCPError(
            "base error",
            context={"server": "s1"},
        )
        result = str(err)
        assert "base error" in result
        assert "server" in result
