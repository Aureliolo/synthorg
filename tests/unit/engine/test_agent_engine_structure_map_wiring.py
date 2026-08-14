"""Structure-map query tool reaches the agent's per-run registry.

The factory is built at boot by brownfield intake and parked on the
engine slice; this pins that ``AgentEngine._make_tool_invoker`` resolves
it through its provider and adds ``query_structure_map`` to the per-task
registry only when a project scope exists.
"""

from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.engine.agent_engine import AgentEngine
from synthorg.persistence.codebase_structure_map_protocol import (
    CodebaseStructureMapRepository,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.structure_map.tool_factory import StructureMapToolFactory
from tests._shared import mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

pytestmark = pytest.mark.unit


class _StubTool(BaseTool):
    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="stub")


def _engine(*, wired: bool = False) -> AgentEngine:
    registry = ToolRegistry([_StubTool(name="stub", category=ToolCategory.OTHER)])
    factory = (
        StructureMapToolFactory(
            repository=mock_of[CodebaseStructureMapRepository](),
        )
        if wired
        else None
    )
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        structure_map_tool_factory_provider=lambda: factory,
    )


def _names(engine: AgentEngine, *, project_id: str | None) -> set[str]:
    invoker = engine._make_tool_invoker(
        make_e2e_identity(), project_id=project_id, memory_strategy=None
    )
    assert invoker is not None
    return {d.name for d in invoker.get_permitted_definitions()}


class TestAgentEngineStructureMapWiring:
    """``query_structure_map`` registers only with a project scope + factory."""

    def test_tool_registered_with_project_scope(self) -> None:
        names = _names(_engine(wired=True), project_id="proj-1")
        assert "query_structure_map" in names

    def test_no_tool_without_project_scope(self) -> None:
        names = _names(_engine(wired=True), project_id=None)
        assert "query_structure_map" not in names

    def test_no_tool_when_factory_absent(self) -> None:
        names = _names(_engine(wired=False), project_id="proj-1")
        assert "query_structure_map" not in names
