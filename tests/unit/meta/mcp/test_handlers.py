"""Unit tests for MCP tool handlers."""

import json

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.meta.mcp.handlers.common import (
    make_handlers_for_tools,
    make_placeholder_handler,
)
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.observability.events.mcp import MCP_HANDLER_NOT_IMPLEMENTED
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestPlaceholderHandler:
    """Tests for the placeholder handler factory.

    The scaffold now delegates to the canonical ``not_supported()``
    envelope so unwired tools emit ``status="error"`` /
    ``domain_code="not_supported"`` -- the same contract every real
    handler ships.  The ``MCP_HANDLER_NOT_IMPLEMENTED`` WARNING event
    continues to fire so ops alerting keeps working.
    """

    async def test_returns_not_supported_envelope(self) -> None:
        handler = make_placeholder_handler("synthorg_test_get")
        result = await handler(app_state=mock_of[AppState](), arguments={})
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        assert "synthorg_test_get" in body["message"]

    async def test_logs_warning_with_handler_not_implemented_event(self) -> None:
        """Placeholder log stays at WARNING + keeps the HYG-1 event name.

        Ops alerting depends on this level + name being stable.
        """
        handler = make_placeholder_handler("synthorg_test_get")
        with structlog.testing.capture_logs() as logs:
            await handler(app_state=mock_of[AppState](), arguments={})
        events = [e for e in logs if e.get("event") == MCP_HANDLER_NOT_IMPLEMENTED]
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"
        assert events[0]["tool_name"] == "synthorg_test_get"

    async def test_accepts_actor_kwarg(self) -> None:
        """Handler protocol now includes ``actor``; placeholder ignores it."""
        handler = make_placeholder_handler("synthorg_test_get")
        result = await handler(
            app_state=mock_of[AppState](),
            arguments={},
            actor=None,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"


class TestMakeHandlersForTools:
    """Tests for batch handler creation."""

    def test_creates_handlers_for_all_tools(self) -> None:
        handlers = make_handlers_for_tools(("tool_a", "tool_b", "tool_c"))
        assert len(handlers) == 3
        assert "tool_a" in handlers
        assert "tool_b" in handlers
        assert "tool_c" in handlers

    async def test_all_handlers_are_callable(self) -> None:
        handlers = make_handlers_for_tools(("tool_a",))
        result = await handlers["tool_a"](app_state=mock_of[AppState](), arguments={})
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        assert "tool_a" in body["message"]


class TestBuildHandlerMap:
    """Tests for the unified handler map builder."""

    def test_builds_handler_map(self) -> None:
        handlers = build_handler_map()
        assert len(handlers) > 100

    def test_no_duplicate_keys(self) -> None:
        # build_handler_map raises ValueError on duplicates
        handlers = build_handler_map()
        assert len(handlers) > 0  # MappingProxyType wraps the dict

    def test_handler_count_matches_tool_count(self) -> None:
        """Every tool should have a matching handler."""
        registry = build_full_registry()
        handlers = build_handler_map()
        tool_names = set(registry.get_names())
        handler_keys = set(handlers.keys())
        missing = tool_names - handler_keys
        assert not missing, f"Tools without handlers: {missing}"

    def test_no_orphan_handlers(self) -> None:
        """Every handler should map to a registered tool."""
        registry = build_full_registry()
        handlers = build_handler_map()
        tool_names = set(registry.get_names())
        handler_keys = set(handlers.keys())
        orphans = handler_keys - tool_names
        assert not orphans, f"Handlers without tools: {orphans}"


class TestEndToEndInvocation:
    """End-to-end test: registry + handlers + invoker."""

    async def test_invoke_placeholder_via_invoker(self) -> None:
        """End-to-end dispatch test with a synthetic placeholder tool.

        Uses an explicit synthetic tool registered into an ad-hoc
        registry so the test stays stable as domain handlers migrate
        off the placeholder scaffold.
        """
        from synthorg.meta.mcp.registry import DomainToolRegistry, MCPToolDef

        synth_tool = MCPToolDef(
            name="synthorg_synth_placeholder",
            description="test placeholder",
            parameters={"type": "object", "properties": {}},
            capability="synth:read",
            handler_key="synthorg_synth_placeholder",
        )
        registry = DomainToolRegistry()
        registry.register(synth_tool)
        registry.freeze()

        placeholder = make_placeholder_handler("synthorg_synth_placeholder")
        handlers = {"synthorg_synth_placeholder": placeholder}
        invoker = MCPToolInvoker(registry, handlers)

        result = await invoker.invoke(
            "synthorg_synth_placeholder",
            {"offset": 0, "limit": 10},
            app_state=mock_of[AppState](),
        )
        assert result.is_error is False
        body = json.loads(result.content)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        assert "synthorg_synth_placeholder" in body["message"]

    async def test_invoke_unknown_tool(self) -> None:
        registry = build_full_registry()
        handlers = build_handler_map()
        invoker = MCPToolInvoker(registry, handlers)

        result = await invoker.invoke(
            "synthorg_nonexistent_tool",
            {},
            app_state=mock_of[AppState](),
        )
        assert result.is_error is True
