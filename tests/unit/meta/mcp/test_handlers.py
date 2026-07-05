"""Unit tests for MCP tool handlers."""

import json

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.meta.mcp.handlers.common import not_supported
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.observability.events.mcp import MCP_HANDLER_NOT_IMPLEMENTED
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestNotSupported:
    """Tests for the ``not_supported`` envelope helper.

    Concrete handlers whose backing service is wired but whose selected
    backend cannot perform an operation return this envelope. Ops alerting
    depends on the ``MCP_HANDLER_NOT_IMPLEMENTED`` WARNING event's level +
    name staying stable.
    """

    def test_returns_not_supported_envelope(self) -> None:
        body = json.loads(not_supported("synthorg_test_get", "backend lacks fine-tune"))
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        assert body["message"] == "backend lacks fine-tune"

    def test_logs_warning_with_handler_not_implemented_event(self) -> None:
        """Log stays at WARNING + keeps the event name ops alerting depends on."""
        with structlog.testing.capture_logs() as logs:
            not_supported("synthorg_test_get", "backend lacks fine-tune")
        events = [e for e in logs if e.get("event") == MCP_HANDLER_NOT_IMPLEMENTED]
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"
        assert events[0]["tool_name"] == "synthorg_test_get"


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
