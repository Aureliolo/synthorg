"""Boundary tests for the MCP invoker dispatch surface.

The invoker validates the inbound ``arguments`` dict through
:func:`synthorg.api.boundary.parse_typed` against the tool's
declared ``args_model``, so a malformed call emits
``api.boundary.validation_failed`` before reaching the handler. The
``ArgumentValidationError`` envelope translation is preserved -- the
boundary helper logs and re-raises; the invoker formats the
re-raised :class:`ValidationError` into the wire response.
"""

import json

import pytest
import structlog
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.meta.mcp.registry import DomainToolRegistry
from synthorg.meta.mcp.tool_builder import read_tool
from tests._shared import mock_of


class _SampleArgs(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Required name")
    count: int = Field(ge=0, description="Non-negative count")


async def _ok_handler(
    *,
    app_state: object,
    arguments: dict[str, object],
    actor: object = None,
) -> str:
    return json.dumps({"status": "ok", "data": arguments})


def _build_registry_and_invoker() -> tuple[DomainToolRegistry, MCPToolInvoker]:
    registry = DomainToolRegistry()
    tool = read_tool(
        domain="boundary_test",
        action="invoke",
        description="boundary test",
        properties={"name": {"type": "string"}, "count": {"type": "integer"}},
        required=("name", "count"),
        args_model=_SampleArgs,
    )
    registry.register(tool)
    handlers: dict[str, ToolHandler] = {tool.handler_key: _ok_handler}
    invoker = MCPToolInvoker(registry, handlers)
    return registry, invoker


_TOOL_NAME = "synthorg_boundary_test_invoke"


@pytest.mark.unit
class TestMcpInvokerBoundary:
    async def test_valid_arguments_round_trip(self) -> None:
        _, invoker = _build_registry_and_invoker()
        result = await invoker.invoke(
            _TOOL_NAME,
            {"name": "alice", "count": 7},
            app_state=mock_of[AppState](),
        )
        assert result.is_error is False
        body = json.loads(result.content)
        assert body["status"] == "ok"

    async def test_missing_required_field_rejected(self) -> None:
        _, invoker = _build_registry_and_invoker()
        result = await invoker.invoke(
            _TOOL_NAME,
            {"count": 7},
            app_state=mock_of[AppState](),
        )
        assert result.is_error is True
        body = json.loads(result.content)
        assert body["error_type"] == "ArgumentValidationError"
        assert body["domain_code"] == "invalid_argument"

    async def test_extra_field_rejected(self) -> None:
        _, invoker = _build_registry_and_invoker()
        result = await invoker.invoke(
            _TOOL_NAME,
            {"name": "alice", "count": 7, "extra": "boom"},
            app_state=mock_of[AppState](),
        )
        assert result.is_error is True

    async def test_validation_failure_emits_boundary_log(self) -> None:
        _, invoker = _build_registry_and_invoker()
        with structlog.testing.capture_logs() as logs:
            await invoker.invoke(
                _TOOL_NAME,
                {"name": "alice", "count": -1},
                app_state=mock_of[AppState](),
            )
        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        record = boundary_logs[0]
        assert record["boundary"] == "mcp.tool"
        assert record["log_level"] == "warning"
