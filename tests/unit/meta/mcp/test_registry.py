"""Unit tests for MCP tool registry."""

import pytest
from pydantic import ValidationError

from synthorg.meta.mcp.errors import ToolRegistryFrozenError
from synthorg.meta.mcp.registry import DomainToolRegistry
from tests.unit.meta.mcp.conftest import make_tool

pytestmark = pytest.mark.unit


class TestMCPToolDef:
    """MCPToolDef model tests."""

    def test_frozen_model(self) -> None:
        tool = make_tool()
        with pytest.raises(ValidationError):
            tool.name = "changed"  # type: ignore[misc]

    def test_required_fields(self) -> None:
        tool = make_tool()
        assert tool.name == "synthorg_test_get"
        assert tool.description == "A test tool"
        assert tool.capability == "test:read"
        assert tool.handler_key == "synthorg_test_get"

    @pytest.mark.parametrize(
        ("overrides", "match"),
        [
            ({"name": ""}, None),
            ({"name": "   "}, None),
            ({"name": "invalid_name"}, "synthorg_"),
            ({"description": ""}, None),
            ({"capability": "no_colon"}, "domain:action"),
        ],
        ids=[
            "blank-name",
            "whitespace-name",
            "name-without-prefix",
            "blank-description",
            "bad-capability-format",
        ],
    )
    def test_rejects_invalid_fields(
        self, overrides: dict[str, str], match: str | None
    ) -> None:
        with pytest.raises(ValidationError, match=match):
            make_tool(**overrides)


class TestDomainToolRegistry:
    """DomainToolRegistry tests."""

    def test_register_and_get(self) -> None:
        registry = DomainToolRegistry()
        tool = make_tool()
        registry.register(tool)
        registry.freeze()
        retrieved = registry.get("synthorg_test_get")
        assert retrieved == tool
        assert retrieved is not tool  # deep copy, not same object

    def test_get_returns_mutation_safe_copy(self) -> None:
        """Mutating get() result must not affect registry state."""
        registry = DomainToolRegistry()
        registry.register(make_tool())
        registry.freeze()
        retrieved = registry.get("synthorg_test_get")
        retrieved.parameters["injected"] = "bad"
        fresh = registry.get("synthorg_test_get")
        assert "injected" not in fresh.parameters

    def test_register_many(self) -> None:
        registry = DomainToolRegistry()
        t1 = make_tool("synthorg_tasks_list")
        t2 = make_tool("synthorg_tasks_get")
        registry.register_many((t1, t2))
        registry.freeze()
        assert registry.tool_count == 2

    def test_duplicate_name_raises(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool("synthorg_test_get"))
        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry.register(make_tool("synthorg_test_get"))

    def test_freeze_prevents_registration(self) -> None:
        registry = DomainToolRegistry()
        registry.freeze()
        with pytest.raises(ToolRegistryFrozenError, match="frozen"):
            registry.register(make_tool())

    def test_freeze_idempotent(self) -> None:
        registry = DomainToolRegistry()
        registry.freeze()
        registry.freeze()  # Should not raise
        assert registry.frozen is True

    def test_auto_freeze_on_get(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool())
        assert registry.frozen is False
        _ = registry.get("synthorg_test_get")
        assert registry.frozen is True

    def test_auto_freeze_on_get_all(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool())
        assert registry.frozen is False
        _ = registry.get_all()
        assert registry.frozen is True

    def test_get_all_sorted(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool("synthorg_z_last"))
        registry.register(make_tool("synthorg_a_first"))
        tools = registry.get_all()
        assert tools[0].name == "synthorg_a_first"
        assert tools[1].name == "synthorg_z_last"

    def test_get_unknown_raises_keyerror(self) -> None:
        registry = DomainToolRegistry()
        registry.freeze()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_get_tool_definitions_returns_dicts(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool("synthorg_test_one"))
        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "synthorg_test_one"
        assert "description" in defs[0]
        assert "parameters" in defs[0]
        # Verify no capability or handler_key in the dict
        assert "capability" not in defs[0]
        assert "handler_key" not in defs[0]

    def test_get_names_sorted(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool("synthorg_b_tool"))
        registry.register(make_tool("synthorg_a_tool"))
        names = registry.get_names()
        assert names == ("synthorg_a_tool", "synthorg_b_tool")

    def test_as_mapping_read_only(self) -> None:
        registry = DomainToolRegistry()
        registry.register(make_tool())
        mapping = registry.as_mapping()
        assert "synthorg_test_get" in mapping
        with pytest.raises(TypeError):
            mapping["new"] = make_tool("synthorg_new_tool")  # type: ignore[index]

    def test_tool_count(self) -> None:
        registry = DomainToolRegistry()
        assert registry.tool_count == 0
        registry.register(make_tool("synthorg_one_get"))
        assert registry.tool_count == 1
        registry.register(make_tool("synthorg_two_get"))
        assert registry.tool_count == 2

    def test_empty_registry(self) -> None:
        registry = DomainToolRegistry()
        registry.freeze()
        assert registry.get_all() == ()
        assert registry.get_names() == ()
        assert registry.tool_count == 0
