"""Unit tests for MCP tool invoker."""

import json

import pytest

from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.meta.mcp.registry import MCPToolDef
from tests.unit.meta.mcp.conftest import make_test_actor, make_tool, registry_with

pytestmark = pytest.mark.unit


class TestMCPToolInvoker:
    """MCPToolInvoker dispatch tests."""

    async def test_invoke_success(self) -> None:
        tool = make_tool()
        registry = registry_with(tool)

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            return json.dumps({"result": "ok"})

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
        result = await invoker.invoke("synthorg_test_get", {}, app_state=None)
        assert result.is_error is False
        assert json.loads(result.content) == {"result": "ok"}

    async def test_invoke_unknown_tool(self) -> None:
        registry = registry_with()
        invoker = MCPToolInvoker(registry, {})
        result = await invoker.invoke("nonexistent", {}, app_state=None)
        assert result.is_error is True
        body = json.loads(result.content)
        assert "Unknown tool" in body["error"]

    async def test_invoke_no_handler(self) -> None:
        tool = make_tool()
        registry = registry_with(tool)
        # Register tool but no handler
        invoker = MCPToolInvoker(registry, {})
        result = await invoker.invoke("synthorg_test_get", {}, app_state=None)
        assert result.is_error is True
        body = json.loads(result.content)
        assert "No handler" in body["error"]

    async def test_invoke_handler_exception(self) -> None:
        tool = make_tool()
        registry = registry_with(tool)

        async def bad_handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            msg = "something broke"
            raise ValueError(msg)

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": bad_handler})
        result = await invoker.invoke("synthorg_test_get", {}, app_state=None)
        assert result.is_error is True
        body = json.loads(result.content)
        assert body["status"] == "error"
        assert body["error_type"] == "ValueError"
        assert body["domain_code"] == "handler_failure"
        assert body["tool"] == "synthorg_test_get"
        # ``message`` carries the safe-redacted description, not the raw exc text.
        assert isinstance(body["message"], str)

    async def test_invoke_passes_arguments(self) -> None:
        tool = make_tool()
        registry = registry_with(tool)
        captured: dict[str, object] = {}

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            captured.update(arguments)
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
        await invoker.invoke(
            "synthorg_test_get",
            {"key": "value"},
            app_state=None,
        )
        assert captured == {"key": "value"}

    async def test_invoke_passes_app_state(self) -> None:
        tool = make_tool()
        registry = registry_with(tool)
        captured: list[object] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            captured.append(app_state)
            return json.dumps({"ok": True})

        sentinel = object()
        invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
        await invoker.invoke("synthorg_test_get", {}, app_state=sentinel)
        assert captured[0] is sentinel

    async def test_invoke_passes_actor_when_provided(self) -> None:
        """Invoker threads ``actor`` through to the handler."""
        tool = make_tool()
        registry = registry_with(tool)
        captured: list[object] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            captured.append(actor)
            return json.dumps({"ok": True})

        actor_sentinel = make_test_actor()
        invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
        await invoker.invoke(
            "synthorg_test_get",
            {},
            app_state=None,
            actor=actor_sentinel,
        )
        assert captured[0] is actor_sentinel

    async def test_invoke_actor_defaults_to_none(self) -> None:
        """When the caller omits ``actor``, the handler receives ``None``."""
        tool = make_tool()
        registry = registry_with(tool)
        captured: list[object] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            captured.append(actor)
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
        await invoker.invoke("synthorg_test_get", {}, app_state=None)
        assert captured[0] is None

    async def test_invoke_handler_key_different_from_name(self) -> None:
        """Tool name and handler_key can differ."""
        tool = MCPToolDef(
            name="synthorg_test_get",
            description="test",
            parameters={"type": "object", "properties": {}},
            capability="test:read",
            handler_key="custom_key",
        )
        registry = registry_with(tool)

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            return json.dumps({"handler": "custom"})

        invoker = MCPToolInvoker(registry, {"custom_key": handler})
        result = await invoker.invoke("synthorg_test_get", {}, app_state=None)
        assert result.is_error is False
        assert json.loads(result.content) == {"handler": "custom"}

    async def test_invoke_reraises_memory_error(self) -> None:
        """MemoryError must propagate, not be caught as an error result."""
        tool = make_tool()
        registry = registry_with(tool)

        async def oom_handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            raise MemoryError

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": oom_handler})
        with pytest.raises(MemoryError):
            await invoker.invoke("synthorg_test_get", {}, app_state=None)

    async def test_invoke_reraises_recursion_error(self) -> None:
        """RecursionError must propagate, not be caught as an error result."""
        tool = make_tool()
        registry = registry_with(tool)

        async def recursion_handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            raise RecursionError

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": recursion_handler})
        with pytest.raises(RecursionError):
            await invoker.invoke("synthorg_test_get", {}, app_state=None)
