"""Tests for ToolRegistry."""

from typing import TYPE_CHECKING

import pytest

from synthorg.providers.models import ToolDefinition
from synthorg.tools.errors import ToolNotFoundError
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from synthorg.tools.base import BaseTool

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestToolRegistryEmpty:
    """Tests for an empty registry."""

    def test_empty_registry_len(self) -> None:
        registry = ToolRegistry([])
        assert len(registry) == 0

    def test_empty_registry_list_tools(self) -> None:
        registry = ToolRegistry([])
        assert registry.list_tools() == ()

    def test_empty_registry_to_definitions(self) -> None:
        registry = ToolRegistry([])
        assert registry.to_definitions() == ()

    def test_empty_registry_get_raises(self) -> None:
        registry = ToolRegistry([])
        with pytest.raises(ToolNotFoundError, match="not registered"):
            registry.get("missing")

    def test_empty_registry_get_shows_none_available(self) -> None:
        registry = ToolRegistry([])
        with pytest.raises(ToolNotFoundError, match=r"\(none\)") as exc_info:
            registry.get("missing")
        assert exc_info.value.context["tool"] == "missing"


@pytest.mark.unit
class TestToolRegistrySingle:
    """Tests for a registry with one tool."""

    def test_len(self, echo_test_tool: BaseTool) -> None:
        registry = ToolRegistry([echo_test_tool])
        assert len(registry) == 1

    def test_get_success(self, echo_test_tool: BaseTool) -> None:
        registry = ToolRegistry([echo_test_tool])
        assert registry.get("echo_test") is echo_test_tool

    def test_contains(self, echo_test_tool: BaseTool) -> None:
        registry = ToolRegistry([echo_test_tool])
        assert "echo_test" in registry

    def test_not_contains(self, echo_test_tool: BaseTool) -> None:
        registry = ToolRegistry([echo_test_tool])
        assert "missing" not in registry


@pytest.mark.unit
class TestToolRegistryMultiple:
    """Tests for a registry with multiple tools."""

    def test_len(self, sample_registry: ToolRegistry) -> None:
        assert len(sample_registry) == 5

    def test_list_tools_sorted(self, sample_registry: ToolRegistry) -> None:
        names = sample_registry.list_tools()
        assert names == tuple(sorted(names))
        assert len(names) == 5

    def test_get_each(self, sample_registry: ToolRegistry) -> None:
        for name in sample_registry.list_tools():
            tool = sample_registry.get(name)
            assert tool.name == name

    def test_get_not_found(self, sample_registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError, match="not registered"):
            sample_registry.get("nonexistent")

    def test_get_not_found_context(self, sample_registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError) as exc_info:
            sample_registry.get("nonexistent")
        assert exc_info.value.context["tool"] == "nonexistent"

    def test_to_definitions(self, sample_registry: ToolRegistry) -> None:
        defs = sample_registry.to_definitions()
        assert len(defs) == 5
        assert all(isinstance(d, ToolDefinition) for d in defs)
        names = [d.name for d in defs]
        assert names == sorted(names)

    def test_contains_non_string(self, sample_registry: ToolRegistry) -> None:
        assert 42 not in sample_registry

    def test_contains_unhashable_type(self, sample_registry: ToolRegistry) -> None:
        assert [1, 2] not in sample_registry


@pytest.mark.unit
class TestToolRegistryDuplicate:
    """Tests for duplicate tool name rejection."""

    def test_duplicate_rejected(self, echo_test_tool: BaseTool) -> None:
        with pytest.raises(ValueError, match="Duplicate tool name"):
            ToolRegistry([echo_test_tool, echo_test_tool])


@pytest.mark.unit
class TestToolRegistryImmutability:
    """Tests for registry immutability."""

    def test_tools_mapping_immutable(self, sample_registry: ToolRegistry) -> None:
        with pytest.raises(TypeError):
            sample_registry._tools["hack"] = None  # type: ignore[index]
