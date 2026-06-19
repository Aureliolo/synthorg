"""Living-docs + research tools reach the agent's per-run registry.

Both factories are built at boot and parked on their state slices; these
tests pin that ``AgentEngine._make_tool_invoker`` resolves them through
their providers and adds the tools to the per-task registry (docs only
when a project scope exists; research always, with an optional scope).
"""

from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.tool_factory import DocsToolFactory
from synthorg.engine.agent_engine import AgentEngine
from synthorg.research.service import ResearchService
from synthorg.research.tool_factory import ResearchToolFactory
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.registry import ToolRegistry
from tests._shared import mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

pytestmark = pytest.mark.unit


class _StubTool(BaseTool):
    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="stub")


def _engine(
    *,
    docs: bool = False,
    research: bool = False,
) -> AgentEngine:
    registry = ToolRegistry([_StubTool(name="stub", category=ToolCategory.OTHER)])
    docs_factory = (
        DocsToolFactory(docs_service=mock_of[DocsService]()) if docs else None
    )
    research_factory = (
        ResearchToolFactory(service=mock_of[ResearchService]()) if research else None
    )
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        docs_tool_factory_provider=lambda: docs_factory,
        research_tool_factory_provider=lambda: research_factory,
    )


def _names(engine: AgentEngine, *, project_id: str | None) -> set[str]:
    invoker = engine._make_tool_invoker(make_e2e_identity(), project_id=project_id)
    assert invoker is not None
    return {d.name for d in invoker.get_permitted_definitions()}


class TestAgentEngineDocsWiring:
    """Docs tools register only with a project scope and a wired factory.

    ``search_living_docs`` (read) is the wiring signal; ``write_living_doc``
    is additionally subject to per-agent write-permission narrowing, which
    is a separate concern from whether the factory reached the registry.
    """

    def test_docs_tools_registered_with_project_scope(self) -> None:
        names = _names(_engine(docs=True), project_id="proj-1")
        assert "search_living_docs" in names

    def test_no_docs_tools_without_project_scope(self) -> None:
        names = _names(_engine(docs=True), project_id=None)
        assert "search_living_docs" not in names

    def test_no_docs_tools_when_factory_absent(self) -> None:
        names = _names(_engine(docs=False), project_id="proj-1")
        assert "search_living_docs" not in names


class TestAgentEngineResearchWiring:
    """The research tool registers whenever its factory is wired."""

    def test_research_tool_registered_with_scope(self) -> None:
        assert "research" in _names(_engine(research=True), project_id="proj-1")

    def test_research_tool_registered_without_scope(self) -> None:
        assert "research" in _names(_engine(research=True), project_id=None)

    def test_no_research_tool_when_factory_absent(self) -> None:
        assert "research" not in _names(_engine(research=False), project_id="proj-1")
