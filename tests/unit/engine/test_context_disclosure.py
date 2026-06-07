"""Tests for AgentContext progressive disclosure state transitions."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.engine.context import AgentContext
from synthorg.hr.seniority import SeniorityLevel


def _make_context(**overrides: object) -> AgentContext:
    """Create a minimal AgentContext for testing."""
    identity = AgentIdentity(
        name="test-agent",
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        personality=PersonalityConfig(),
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )
    defaults: dict[str, object] = {
        "execution_id": "exec-1",
        "identity": identity,
        "started_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)  # type: ignore[arg-type]


# ── Default state ────────────────────────────────────────────────


@pytest.mark.unit
class TestDisclosureDefaults:
    """Tests for disclosure field defaults."""

    def test_loaded_tools_default_empty(self) -> None:
        ctx = _make_context()
        assert ctx.loaded_tools == frozenset()

    def test_loaded_resources_default_empty(self) -> None:
        ctx = _make_context()
        assert ctx.loaded_resources == frozenset()

    def test_tool_load_order_default_empty(self) -> None:
        ctx = _make_context()
        assert ctx.tool_load_order == ()


# ── with_tool_loaded ─────────────────────────────────────────────


@pytest.mark.unit
class TestWithToolLoaded:
    """Tests for with_tool_loaded() state transition."""

    def test_adds_tool_to_loaded(self) -> None:
        ctx = _make_context()
        new_ctx = ctx.with_tool_loaded("read_file")
        assert "read_file" in new_ctx.loaded_tools
        assert "read_file" in new_ctx.tool_load_order

    def test_original_unchanged(self) -> None:
        ctx = _make_context()
        ctx.with_tool_loaded("read_file")
        assert ctx.loaded_tools == frozenset()

    def test_multiple_tools(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_tool_loaded("read_file")
        ctx = ctx.with_tool_loaded("write_file")
        assert ctx.loaded_tools == frozenset({"read_file", "write_file"})
        assert ctx.tool_load_order == ("read_file", "write_file")

    def test_idempotent(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_tool_loaded("read_file")
        ctx2 = ctx.with_tool_loaded("read_file")
        assert ctx2 is ctx  # Same object returned
        assert ctx2.tool_load_order == ("read_file",)

    def test_preserves_insertion_order(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_tool_loaded("c_tool")
        ctx = ctx.with_tool_loaded("a_tool")
        ctx = ctx.with_tool_loaded("b_tool")
        assert ctx.tool_load_order == ("c_tool", "a_tool", "b_tool")


# ── with_tool_unloaded ───────────────────────────────────────────


@pytest.mark.unit
class TestWithToolUnloaded:
    """Tests for with_tool_unloaded() state transition."""

    def test_removes_tool(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_tool_loaded("read_file")
        ctx = ctx.with_tool_unloaded("read_file")
        assert "read_file" not in ctx.loaded_tools
        assert "read_file" not in ctx.tool_load_order

    def test_idempotent_on_unloaded(self) -> None:
        ctx = _make_context()
        ctx2 = ctx.with_tool_unloaded("read_file")
        assert ctx2 is ctx  # Same object returned

    def test_removes_related_resources(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_tool_loaded("read_file")
        ctx = ctx.with_resource_loaded("read_file", "guide")
        ctx = ctx.with_resource_loaded("read_file", "examples")
        ctx = ctx.with_resource_loaded("other_tool", "data")
        ctx = ctx.with_tool_unloaded("read_file")
        assert ("read_file", "guide") not in ctx.loaded_resources
        assert ("read_file", "examples") not in ctx.loaded_resources
        assert ("other_tool", "data") in ctx.loaded_resources

    def test_preserves_order_of_remaining(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_tool_loaded("a")
        ctx = ctx.with_tool_loaded("b")
        ctx = ctx.with_tool_loaded("c")
        ctx = ctx.with_tool_unloaded("b")
        assert ctx.tool_load_order == ("a", "c")
        assert ctx.loaded_tools == frozenset({"a", "c"})


# ── with_resource_loaded ─────────────────────────────────────────


@pytest.mark.unit
class TestWithResourceLoaded:
    """Tests for with_resource_loaded() state transition."""

    def test_adds_resource(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_resource_loaded("read_file", "guide")
        assert ("read_file", "guide") in ctx.loaded_resources

    def test_original_unchanged(self) -> None:
        ctx = _make_context()
        ctx.with_resource_loaded("read_file", "guide")
        assert ctx.loaded_resources == frozenset()

    def test_multiple_resources(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_resource_loaded("read_file", "guide")
        ctx = ctx.with_resource_loaded("read_file", "examples")
        assert len(ctx.loaded_resources) == 2

    def test_idempotent(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_resource_loaded("read_file", "guide")
        ctx2 = ctx.with_resource_loaded("read_file", "guide")
        assert ctx2 is ctx  # Same object returned

    def test_resources_from_different_tools(self) -> None:
        ctx = _make_context()
        ctx = ctx.with_resource_loaded("tool_a", "res1")
        ctx = ctx.with_resource_loaded("tool_b", "res1")
        assert len(ctx.loaded_resources) == 2
        assert ("tool_a", "res1") in ctx.loaded_resources
        assert ("tool_b", "res1") in ctx.loaded_resources


# ── Frozen immutability ──────────────────────────────────────────


@pytest.mark.unit
class TestDisclosureFrozen:
    """Tests that disclosure fields are frozen."""

    def test_cannot_assign_loaded_tools(self) -> None:
        ctx = _make_context()
        with pytest.raises(ValidationError):
            ctx.loaded_tools = frozenset({"x"})  # type: ignore[misc]

    def test_cannot_assign_loaded_resources(self) -> None:
        ctx = _make_context()
        with pytest.raises(ValidationError):
            ctx.loaded_resources = frozenset()  # type: ignore[misc]

    def test_cannot_assign_tool_load_order(self) -> None:
        ctx = _make_context()
        with pytest.raises(ValidationError):
            ctx.tool_load_order = ()  # type: ignore[misc]
