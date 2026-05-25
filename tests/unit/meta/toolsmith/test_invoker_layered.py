# mypy: disable-error-code="explicit-any,explicit-override,unused-awaitable"
"""The real MCPToolInvoker dispatches authored tools via the layered surfaces."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.meta.mcp.registry import DomainToolRegistry
from synthorg.meta.toolsmith.dynamic_registry import (
    DynamicToolRegistry,
    LayeredHandlerMap,
    LayeredToolRegistry,
)
from synthorg.meta.toolsmith.models import ToolBlueprint

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id="bp-1",
        name="synthorg_textkit_slugify",
        description="Slugify text.",
        capability="textkit:slugify",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body="print('x')",
        action_type="code:read",
        created_at=_NOW,
    )


def _echo_factory(blueprint: ToolBlueprint) -> ToolHandler:
    del blueprint

    async def _handler(
        *, app_state: Any, arguments: dict[str, Any], actor: Any = None
    ) -> str:
        del app_state, actor
        return json.dumps({"status": "ok", "data": arguments})

    return _handler


async def _build_invoker() -> tuple[MCPToolInvoker, DynamicToolRegistry]:
    static = DomainToolRegistry()
    static.freeze()
    dynamic = DynamicToolRegistry(handler_factory=_echo_factory)
    registry = LayeredToolRegistry(static, dynamic)
    handlers = LayeredHandlerMap({}, dynamic)
    return MCPToolInvoker(registry, handlers), dynamic


class TestInvokerLayeredDispatch:
    async def test_dispatches_authored_tool_after_registration(self) -> None:
        invoker, dynamic = await _build_invoker()
        await dynamic.register(_blueprint())

        result = await invoker.invoke(
            "synthorg_textkit_slugify",
            {"text": "Hello"},
            app_state=None,
        )
        assert result.is_error is False
        envelope = json.loads(result.content)
        assert envelope["data"] == {"text": "Hello"}

    async def test_args_model_validation_rejects_extra(self) -> None:
        invoker, dynamic = await _build_invoker()
        await dynamic.register(_blueprint())

        result = await invoker.invoke(
            "synthorg_textkit_slugify",
            {"text": "Hello", "bogus": 1},
            app_state=None,
        )
        assert result.is_error is True
        envelope = json.loads(result.content)
        assert envelope["domain_code"] == "invalid_argument"

    async def test_args_model_validation_requires_field(self) -> None:
        invoker, dynamic = await _build_invoker()
        await dynamic.register(_blueprint())

        result = await invoker.invoke(
            "synthorg_textkit_slugify",
            {},
            app_state=None,
        )
        assert result.is_error is True

    async def test_unknown_tool_is_error(self) -> None:
        invoker, _ = await _build_invoker()
        result = await invoker.invoke("synthorg_nope_nope", {}, app_state=None)
        assert result.is_error is True
