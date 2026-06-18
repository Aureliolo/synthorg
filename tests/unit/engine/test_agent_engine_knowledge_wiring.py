"""The knowledge tools reach the agent's per-run registry.

The ``search_knowledge`` / ``ingest_knowledge`` tools are wired per run
inside ``AgentEngine._make_tool_invoker`` via the
``knowledge_tool_factory_provider`` (resolved at per-task time because the
memory-gated substrate wires after the boot engine). These tests pin that
engine-level wiring: when the provider yields a factory the tools appear in
the agent's permitted set; when the provider is absent (substrate off) they
do not.
"""

from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.engine.agent_engine import AgentEngine
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.tool_factory import KnowledgeToolFactory
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


def _factory() -> KnowledgeToolFactory:
    service: KnowledgeService = mock_of[KnowledgeService]()
    return KnowledgeToolFactory(service=service)


def _engine(*, with_factory: bool) -> AgentEngine:
    registry = ToolRegistry([_StubTool(name="stub", category=ToolCategory.OTHER)])
    factory = _factory() if with_factory else None
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        knowledge_tool_factory_provider=lambda: factory,
    )


class TestAgentEngineKnowledgeWiring:
    """Knowledge tools are registered per run only when the substrate is wired."""

    def test_knowledge_tools_registered_when_factory_wired(self) -> None:
        engine = _engine(with_factory=True)

        invoker = engine._make_tool_invoker(make_e2e_identity())

        assert invoker is not None
        names = {d.name for d in invoker.get_permitted_definitions()}
        assert "search_knowledge" in names
        assert "ingest_knowledge" in names

    def test_no_knowledge_tools_when_factory_absent(self) -> None:
        engine = _engine(with_factory=False)

        invoker = engine._make_tool_invoker(make_e2e_identity())

        assert invoker is not None
        names = {d.name for d in invoker.get_permitted_definitions()}
        assert "search_knowledge" not in names
        assert "ingest_knowledge" not in names
