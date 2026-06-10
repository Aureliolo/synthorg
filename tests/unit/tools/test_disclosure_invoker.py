"""Tests for ToolInvoker disclosure-aware methods."""

from collections.abc import Sequence
from typing import Never, override

import pytest

from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.permissions import ToolPermissionChecker
from synthorg.tools.registry import ToolRegistry


class _FakeTool(BaseTool):
    """Minimal tool for invoker tests."""

    def __init__(
        self,
        *,
        name: str = "fake",
        description: str = "A fake tool",
        category: ToolCategory = ToolCategory.FILE_SYSTEM,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            category=category,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


def _build_invoker(
    *,
    tools: Sequence[BaseTool] | None = None,
    checker: ToolPermissionChecker | None = None,
) -> ToolInvoker:
    """Build a ToolInvoker with given tools and optional permission checker."""
    if tools is None:
        tools = [
            _FakeTool(name="alpha", category=ToolCategory.FILE_SYSTEM),
            _FakeTool(name="beta", category=ToolCategory.WEB),
            _FakeTool(name="gamma", category=ToolCategory.DEPLOYMENT),
        ]
    registry = ToolRegistry(tools)
    return ToolInvoker(registry, permission_checker=checker)


# ── get_l1_summaries ────────────────────────────────────────────


@pytest.mark.unit
class TestGetL1Summaries:
    """Tests for ToolInvoker.get_l1_summaries()."""

    def test_no_checker_returns_all(self) -> None:
        invoker = _build_invoker()
        summaries = invoker.get_l1_summaries()
        assert len(summaries) == 3
        names = [s.name for s in summaries]
        assert names == ["alpha", "beta", "gamma"]

    def test_with_checker_filters(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        invoker = _build_invoker(checker=checker)
        summaries = invoker.get_l1_summaries()
        names = {s.name for s in summaries}
        # SANDBOXED allows FILE_SYSTEM but not WEB or DEPLOYMENT
        assert "alpha" in names
        assert "beta" not in names
        assert "gamma" not in names


# ── get_loaded_definitions ──────────────────────────────────────


@pytest.mark.unit
class TestGetLoadedDefinitions:
    """Tests for ToolInvoker.get_loaded_definitions()."""

    def test_empty_loaded_returns_only_discovery(self) -> None:
        invoker = _build_invoker()
        defs = invoker.get_loaded_definitions(frozenset())
        names = {d.name for d in defs}
        # No tools loaded, no discovery tools registered,
        # so no definitions returned
        assert names == set()

    def test_loaded_tools_included(self) -> None:
        invoker = _build_invoker()
        defs = invoker.get_loaded_definitions(frozenset({"alpha", "beta"}))
        names = {d.name for d in defs}
        assert "alpha" in names
        assert "beta" in names
        assert "gamma" not in names

    def test_discovery_tool_names_always_included(self) -> None:
        tools = [
            _FakeTool(name="list_tools", category=ToolCategory.MEMORY),
            _FakeTool(name="load_tool", category=ToolCategory.MEMORY),
            _FakeTool(name="load_tool_resource", category=ToolCategory.MEMORY),
            _FakeTool(name="alpha", category=ToolCategory.FILE_SYSTEM),
        ]
        invoker = _build_invoker(tools=tools)
        defs = invoker.get_loaded_definitions(frozenset())
        names = {d.name for d in defs}
        assert "list_tools" in names
        assert "load_tool" in names
        assert "load_tool_resource" in names
        assert "alpha" not in names

    def test_loaded_plus_discovery(self) -> None:
        tools = [
            _FakeTool(name="list_tools", category=ToolCategory.MEMORY),
            _FakeTool(name="load_tool", category=ToolCategory.MEMORY),
            _FakeTool(name="load_tool_resource", category=ToolCategory.MEMORY),
            _FakeTool(name="alpha", category=ToolCategory.FILE_SYSTEM),
            _FakeTool(name="beta", category=ToolCategory.WEB),
        ]
        invoker = _build_invoker(tools=tools)
        defs = invoker.get_loaded_definitions(frozenset({"alpha"}))
        names = {d.name for d in defs}
        assert names == {"list_tools", "load_tool", "load_tool_resource", "alpha"}

    def test_sorted_by_name(self) -> None:
        tools = [
            _FakeTool(name="zebra", category=ToolCategory.FILE_SYSTEM),
            _FakeTool(name="aardvark", category=ToolCategory.FILE_SYSTEM),
        ]
        invoker = _build_invoker(tools=tools)
        defs = invoker.get_loaded_definitions(frozenset({"zebra", "aardvark"}))
        names = [d.name for d in defs]
        assert names == ["aardvark", "zebra"]


# ── error isolation ─────────────────────────────────────────────


class _FaultyTool(_FakeTool):
    """A tool whose disclosure methods raise a non-critical error."""

    @override
    def to_l1_metadata(self) -> Never:
        raise ValueError

    @override
    def to_definition(self) -> Never:
        raise ValueError

    @override
    def to_l2_body(self) -> Never:
        raise ValueError

    @override
    def get_l3_resources(self) -> Never:
        raise ValueError


class _OomTool(_FakeTool):
    """A tool whose L1 metadata raises an interpreter-critical error."""

    @override
    def to_l1_metadata(self) -> Never:
        raise MemoryError


@pytest.mark.unit
class TestDiscoveryErrorIsolation:
    """A non-critical failure in one tool's disclosure methods is logged and
    skipped so a single malformed tool cannot break discovery for the rest,
    while interpreter-critical errors propagate via ``reraise_critical``.
    """

    def test_l1_summary_skips_faulty_tool(self) -> None:
        invoker = _build_invoker(
            tools=[_FaultyTool(name="faulty"), _FakeTool(name="good")]
        )
        names = {m.name for m in invoker.get_l1_summaries()}
        assert names == {"good"}

    def test_loaded_definitions_skips_faulty_tool(self) -> None:
        invoker = _build_invoker(
            tools=[_FaultyTool(name="faulty"), _FakeTool(name="good")]
        )
        names = {
            d.name
            for d in invoker.get_loaded_definitions(frozenset({"faulty", "good"}))
        }
        # Per-tool isolation: the faulty tool is skipped, the healthy one kept.
        assert "good" in names
        assert "faulty" not in names

    def test_l2_body_returns_none_on_failure(self) -> None:
        invoker = _build_invoker(tools=[_FaultyTool(name="faulty")])
        assert invoker.get_l2_body("faulty") is None

    def test_l3_resource_returns_none_on_failure(self) -> None:
        invoker = _build_invoker(tools=[_FaultyTool(name="faulty")])
        assert invoker.get_l3_resource("faulty", "res-1") is None

    def test_l1_summary_skips_registry_lookup_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invoker = _build_invoker(tools=[_FakeTool(name="alpha")])

        def _boom(_name: str) -> object:
            raise ValueError

        monkeypatch.setattr(invoker._registry, "get", _boom)
        assert invoker.get_l1_summaries() == ()

    def test_l2_body_returns_none_on_registry_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invoker = _build_invoker(tools=[_FakeTool(name="alpha")])

        def _boom(_name: str) -> object:
            raise ValueError

        monkeypatch.setattr(invoker._registry, "get", _boom)
        assert invoker.get_l2_body("alpha") is None

    def test_critical_error_propagates(self) -> None:
        invoker = _build_invoker(tools=[_OomTool(name="oom")])
        with pytest.raises(MemoryError):
            invoker.get_l1_summaries()
