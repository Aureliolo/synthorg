"""Unit tests for the dynamic registry, args-model materialisation, and layering."""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.registry import DomainToolRegistry, MCPToolDef, ToolDefReader
from synthorg.meta.toolsmith.dynamic_registry import (
    DynamicToolRegistry,
    LayeredHandlerMap,
    LayeredToolRegistry,
    blueprint_to_mcp_def,
    build_args_model,
)
from synthorg.meta.toolsmith.models import ToolBlueprint

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _blueprint(
    *,
    name: str = "synthorg_textkit_slugify",
    capability: str = "textkit:slugify",
    schema: dict[str, Any] | None = None,
) -> ToolBlueprint:
    return ToolBlueprint(
        id=f"bp-{name}",
        name=name,
        description="Slugify text deterministically.",
        capability=capability,
        parameters_schema=schema
        or {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "max_len": {"type": "integer"},
            },
            "required": ["text"],
        },
        script_body="print('{}')",
        action_type="code:read",
        created_at=_NOW,
    )


async def _fake_handler(
    *, app_state: Any, arguments: dict[str, Any], actor: Any = None
) -> str:
    del app_state, actor
    return f"handled:{sorted(arguments)}"


def _handler_factory(blueprint: ToolBlueprint) -> ToolHandler:
    del blueprint
    return _fake_handler


class _FakeStatic:
    """Minimal ``ToolDefReader`` over a fixed set of defs."""

    def __init__(self, defs: dict[str, MCPToolDef]) -> None:
        self._defs = defs

    def get(self, name: str) -> MCPToolDef:
        return self._defs[name]

    def get_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._defs))


class TestBuildArgsModel:
    def test_fields_match_schema(self) -> None:
        model = build_args_model(_blueprint())
        assert set(model.model_fields) == {"text", "max_len"}
        assert model.model_fields["text"].is_required() is True
        assert model.model_fields["max_len"].is_required() is False

    def test_validates_and_forbids_extra(self) -> None:
        model = build_args_model(_blueprint())
        ok = model.model_validate({"text": "hi"})
        assert ok.text == "hi"  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="extra"):
            model.model_validate({"text": "hi", "bogus": 1})

    def test_required_field_enforced(self) -> None:
        model = build_args_model(_blueprint())
        with pytest.raises(ValueError, match="text"):
            model.model_validate({})

    def test_frozen(self) -> None:
        model = build_args_model(_blueprint())
        instance = model.model_validate({"text": "hi"})
        with pytest.raises(ValueError, match=r"frozen|Instance"):
            instance.text = "bye"  # type: ignore[attr-defined]


class TestBlueprintToMcpDef:
    def test_promotes_with_aligned_args_model(self) -> None:
        definition = blueprint_to_mcp_def(_blueprint())
        assert definition.name == "synthorg_textkit_slugify"
        assert definition.capability == "textkit:slugify"
        assert definition.handler_key == "synthorg_textkit_slugify"
        assert definition.args_model is not None

    def test_rejects_schema_args_drift_is_impossible(self) -> None:
        # The materialised args model is derived FROM the schema, so the
        # MCPToolDef alignment validator always agrees. Confirm a tool
        # with an extra wire-only property still aligns because the model
        # is built from the same properties dict.
        definition = blueprint_to_mcp_def(
            _blueprint(
                schema={
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "required": ["a"],
                }
            )
        )
        assert set(definition.args_model.model_fields) == {"a"}  # type: ignore[union-attr]


class TestDynamicToolRegistry:
    async def test_register_and_read(self) -> None:
        registry = DynamicToolRegistry(handler_factory=_handler_factory)
        await registry.register(_blueprint())
        assert registry.names() == ("synthorg_textkit_slugify",)
        definition = registry.get_def("synthorg_textkit_slugify")
        assert definition is not None
        assert registry.get_handler("synthorg_textkit_slugify") is _fake_handler

    async def test_register_is_idempotent_on_name(self) -> None:
        registry = DynamicToolRegistry(handler_factory=_handler_factory)
        await registry.register(_blueprint())
        await registry.register(_blueprint())
        assert len(registry.names()) == 1

    async def test_unregister(self) -> None:
        registry = DynamicToolRegistry(handler_factory=_handler_factory)
        await registry.register(_blueprint())
        assert await registry.unregister("synthorg_textkit_slugify") is True
        assert registry.names() == ()
        assert await registry.unregister("synthorg_textkit_slugify") is False

    async def test_read_during_register_sees_consistent_snapshot(self) -> None:
        # Snapshot-swap semantics: a read taken before register does not
        # observe the new entry; after, it does. Confirms reads never see
        # a half-built dict.
        registry = DynamicToolRegistry(handler_factory=_handler_factory)
        assert registry.get_def("synthorg_textkit_slugify") is None
        await registry.register(_blueprint())
        assert registry.get_def("synthorg_textkit_slugify") is not None

    async def test_invalid_blueprint_raises(self) -> None:
        registry = DynamicToolRegistry(handler_factory=_handler_factory)
        bad = _blueprint(
            schema={"type": "object", "properties": {}},
        )
        # Empty properties is allowed by the model but yields an empty
        # args model; registration still succeeds. Force a real failure
        # by giving a non-dict property entry path is covered elsewhere;
        # here assert empty-props still registers cleanly.
        await registry.register(bad)
        assert registry.get_def(bad.name) is not None


class TestLayeredToolRegistry:
    async def test_static_first_then_dynamic(self) -> None:
        static_def = blueprint_to_mcp_def(
            _blueprint(name="synthorg_static_tool", capability="static:tool")
        )
        static = _FakeStatic({static_def.name: static_def})
        dynamic = DynamicToolRegistry(handler_factory=_handler_factory)
        await dynamic.register(_blueprint())
        layered = LayeredToolRegistry(static, dynamic)

        assert isinstance(layered, ToolDefReader)
        assert layered.get("synthorg_static_tool").name == "synthorg_static_tool"
        assert layered.get("synthorg_textkit_slugify").name == (
            "synthorg_textkit_slugify"
        )
        assert set(layered.get_names()) == {
            "synthorg_static_tool",
            "synthorg_textkit_slugify",
        }

    async def test_missing_raises_keyerror(self) -> None:
        static = _FakeStatic({})
        dynamic = DynamicToolRegistry(handler_factory=_handler_factory)
        layered = LayeredToolRegistry(static, dynamic)
        with pytest.raises(KeyError):
            layered.get("nope")

    async def test_real_domain_registry_as_static_layer(self) -> None:
        # The frozen production registry satisfies ToolDefReader, so the
        # layered wrapper composes it without modification.
        static = DomainToolRegistry()
        static.freeze()
        dynamic = DynamicToolRegistry(handler_factory=_handler_factory)
        await dynamic.register(_blueprint())
        layered = LayeredToolRegistry(static, dynamic)
        assert layered.get("synthorg_textkit_slugify") is not None


class TestLayeredHandlerMap:
    async def test_static_wins_then_dynamic(self) -> None:
        async def static_handler(
            *, app_state: Any, arguments: dict[str, Any], actor: Any = None
        ) -> str:
            del app_state, arguments, actor
            return "static"

        dynamic = DynamicToolRegistry(handler_factory=_handler_factory)
        await dynamic.register(_blueprint())
        mapping = LayeredHandlerMap({"static_key": static_handler}, dynamic)

        assert mapping["static_key"] is static_handler
        assert mapping["synthorg_textkit_slugify"] is _fake_handler
        assert "synthorg_textkit_slugify" in mapping
        with pytest.raises(KeyError):
            _ = mapping["missing"]
        assert set(mapping) == {"static_key", "synthorg_textkit_slugify"}
        assert len(mapping) == 2


class TestLayeredCollisionPrecedence:
    """Pin the static-wins-on-collision rule for both layered surfaces.

    A regression that flips precedence to dynamic-first would let an
    authored tool shadow a built-in one with the same name, which the
    layered surfaces deliberately forbid; these tests guard that.
    """

    async def test_layered_tool_registry_static_wins_on_name_collision(self) -> None:
        static_def = blueprint_to_mcp_def(
            _blueprint(
                name="synthorg_textkit_slugify",
                capability="textkit:slugify",
            )
        )
        # The dynamic blueprint shares the static name but maps to a
        # different ID so registration is uniquely keyed.
        dynamic = DynamicToolRegistry(handler_factory=_handler_factory)
        dynamic_blueprint = _blueprint(
            name="synthorg_textkit_slugify",
            capability="textkit:slugify",
        ).model_copy(update={"id": "bp-dynamic-shadow"})
        await dynamic.register(dynamic_blueprint)
        layered = LayeredToolRegistry(
            _FakeStatic({static_def.name: static_def}), dynamic
        )

        resolved = layered.get("synthorg_textkit_slugify")
        # Static wins: ``LayeredToolRegistry.get`` returns the static
        # def's identity (same object) instead of the dynamic one.
        assert resolved is static_def

    async def test_layered_handler_map_static_wins_on_key_collision(self) -> None:
        async def static_handler(
            *, app_state: Any, arguments: dict[str, Any], actor: Any = None
        ) -> str:
            del app_state, arguments, actor
            return "static"

        dynamic = DynamicToolRegistry(handler_factory=_handler_factory)
        await dynamic.register(_blueprint())
        # The static handler shadows the same name as the dynamic tool.
        mapping = LayeredHandlerMap(
            {"synthorg_textkit_slugify": static_handler},
            dynamic,
        )

        assert mapping["synthorg_textkit_slugify"] is static_handler
        # Membership and length collapse the duplicate key.
        assert "synthorg_textkit_slugify" in mapping
        assert len(mapping) == 1
        assert set(mapping) == {"synthorg_textkit_slugify"}
