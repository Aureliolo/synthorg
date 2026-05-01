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


class TestMCPToolInvokerArgsModelValidation:
    """Typed-args validation at the invoker boundary."""

    @staticmethod
    def _tool_with_args_model() -> MCPToolDef:
        from pydantic import BaseModel, ConfigDict

        from synthorg.core.types import NotBlankStr

        class _SampleArgs(BaseModel):
            model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
            name: NotBlankStr
            count: int = 1

        return MCPToolDef(
            name="synthorg_test_validated",
            description="A tool that validates its args",
            parameters=_SampleArgs.model_json_schema(),
            capability="test:read",
            handler_key="synthorg_test_validated",
            args_model=_SampleArgs,
        )

    async def test_valid_args_dispatch_to_handler(self) -> None:
        """When ``args_model`` accepts the args, the handler is invoked."""
        tool = self._tool_with_args_model()
        registry = registry_with(tool)
        captured: list[dict[str, object]] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            captured.append(dict(arguments))
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_validated": handler})
        result = await invoker.invoke(
            "synthorg_test_validated",
            {"name": "alice", "count": 7},
            app_state=None,
        )
        assert result.is_error is False
        assert captured == [{"name": "alice", "count": 7}]

    async def test_handler_receives_normalized_args_with_defaults(self) -> None:
        """The Phase 4 contract: defaults / coercions reach the handler.

        ``_SampleArgs.count`` defaults to ``1``.  When the caller omits
        it, the handler must still see ``count=1`` because the invoker
        forwards the validated ``model_dump(mode="python")``, not the
        raw caller dict.  A regression that re-passes the raw input
        would silently strip the default and let handlers re-implement
        defaulting, defeating the typed-boundary promise.
        """
        tool = self._tool_with_args_model()
        registry = registry_with(tool)
        captured: list[dict[str, object]] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            captured.append(dict(arguments))
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_validated": handler})
        # Caller omits ``count`` -- handler should still see the default.
        result = await invoker.invoke(
            "synthorg_test_validated",
            {"name": "alice"},
            app_state=None,
        )
        assert result.is_error is False
        assert captured == [{"name": "alice", "count": 1}]

    async def test_invalid_args_skip_handler_and_return_error_envelope(
        self,
    ) -> None:
        """``args_model`` validation failure short-circuits to an error envelope."""
        tool = self._tool_with_args_model()
        registry = registry_with(tool)
        invoked: list[bool] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            invoked.append(True)
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_validated": handler})
        result = await invoker.invoke(
            "synthorg_test_validated",
            {"name": "   ", "count": "not-an-int"},
            app_state=None,
        )
        assert result.is_error is True
        assert invoked == []  # handler must NOT have been invoked
        body = json.loads(result.content)
        assert body["status"] == "error"
        assert body["error_type"] == "ArgumentValidationError"
        assert body["domain_code"] == "invalid_argument"
        assert body["tool"] == "synthorg_test_validated"
        # Multi-error message: every Pydantic complaint surfaces, not just the first.
        assert "name" in body["message"]
        assert "count" in body["message"]

    async def test_non_dict_arguments_rejected_before_handler(self) -> None:
        """Non-mapping payloads short-circuit before ``model_validate``.

        The MCP wire surface can deliver any JSON value at runtime;
        the invoker has an explicit shape guard that rejects lists,
        strings, etc. before they reach Pydantic.  Without this test
        the guard could regress silently (Pydantic's ``model_validate``
        would still raise on a list, but only after walking it as
        though it were an iterable of pairs).
        """
        tool = self._tool_with_args_model()
        registry = registry_with(tool)
        invoked: list[bool] = []

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            invoked.append(True)
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_validated": handler})
        # ``[("name", "alice")]`` is the classic failure mode
        # ``dict(...)`` would have silently accepted.
        result = await invoker.invoke(
            "synthorg_test_validated",
            [("name", "alice"), ("count", 1)],  # type: ignore[arg-type]
            app_state=None,
        )
        assert result.is_error is True
        assert invoked == []  # handler MUST NOT be reached
        body = json.loads(result.content)
        assert body["error_type"] == "ArgumentValidationError"
        assert body["domain_code"] == "invalid_argument"
        assert "JSON object" in body["message"]

    async def test_missing_required_field_rejected(self) -> None:
        """Missing required fields fail at the boundary."""
        tool = self._tool_with_args_model()
        registry = registry_with(tool)

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_validated": handler})
        result = await invoker.invoke(
            "synthorg_test_validated",
            {},  # missing required `name`
            app_state=None,
        )
        assert result.is_error is True
        body = json.loads(result.content)
        assert body["error_type"] == "ArgumentValidationError"

    async def test_extra_fields_rejected(self) -> None:
        """Unknown fields are rejected (extra=forbid contract)."""
        tool = self._tool_with_args_model()
        registry = registry_with(tool)

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_validated": handler})
        result = await invoker.invoke(
            "synthorg_test_validated",
            {"name": "alice", "unknown": 42},
            app_state=None,
        )
        assert result.is_error is True
        body = json.loads(result.content)
        assert body["error_type"] == "ArgumentValidationError"

    async def test_legacy_tool_without_args_model_still_dispatches(self) -> None:
        """Tools without ``args_model`` still work (legacy ``common_args`` path)."""
        tool = make_tool()  # no args_model
        registry = registry_with(tool)

        async def handler(
            *,
            app_state: object,
            arguments: dict[str, object],
            actor: object = None,
        ) -> str:
            return json.dumps({"ok": True})

        invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
        result = await invoker.invoke(
            "synthorg_test_get",
            {"anything": "works"},
            app_state=None,
        )
        assert result.is_error is False
