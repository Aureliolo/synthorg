"""The project-brain tools reach the agent's per-run registry.

When the project brain is wired and the task carries a ``project_id``,
``AgentEngine._make_tool_invoker`` augments the per-task registry with the
two brain tools (``write_brain_entry`` / ``search_brain``) bound to that
project, so an agent can record decisions and recall them on re-entry. The
factory is resolved through a provider because the brain wires after the
boot engine is built. No project / no brain factory means no brain tools.
"""

from typing import Any, override

import pytest

from synthorg.core.agent import AgentIdentity, ToolPermissions
from synthorg.core.enums import ToolCategory
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.tool_factory import ProjectBrainToolFactory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry
from tests._shared import as_uuid, mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

pytestmark = pytest.mark.unit

_PROJECT_ID = "proj-brain-1"
_BRAIN_TOOL_NAMES = ("write_brain_entry", "search_brain")


class _StubTool(BaseTool):
    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="stub")


def _brain_factory() -> ProjectBrainToolFactory:
    return ProjectBrainToolFactory(brain_service=mock_of[ProjectBrainService]())


def _engine(*, factory: ProjectBrainToolFactory | None) -> AgentEngine:
    registry = ToolRegistry(
        [
            _StubTool(
                name="stub",
                category=ToolCategory.OTHER,
                description="Stub tool used in tests.",
            ),
        ],
    )
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        brain_tool_factory_provider=lambda: factory,
    )


def _elevated_identity() -> AgentIdentity:
    # ELEVATED so the OTHER-category write_brain_entry tool is permitted
    # alongside the MEMORY-category search_brain tool.
    return make_e2e_identity(
        tools=ToolPermissions(access_level=ToolAccessLevel.ELEVATED),
    )


def _resume_task() -> Task:
    return Task(
        id=as_uuid("task-resume-1"),
        title="Resume task",
        description="A development task to be resumed from a parked run.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=_PROJECT_ID,
        created_by="alice",
    )


class TestAgentEngineBrainToolWiring:
    """Brain tools are added only when both factory and project_id are set."""

    def test_brain_tools_registered_for_project_task(self) -> None:
        engine = _engine(factory=_brain_factory())

        invoker = engine._make_tool_invoker(
            _elevated_identity(),
            project_id=_PROJECT_ID,
        )

        assert invoker is not None
        names = [d.name for d in invoker.get_permitted_definitions()]
        for tool_name in _BRAIN_TOOL_NAMES:
            assert tool_name in names

    def test_no_brain_tools_without_project_id(self) -> None:
        engine = _engine(factory=_brain_factory())

        invoker = engine._make_tool_invoker(_elevated_identity(), project_id=None)

        assert invoker is not None
        names = [d.name for d in invoker.get_permitted_definitions()]
        for tool_name in _BRAIN_TOOL_NAMES:
            assert tool_name not in names

    def test_no_brain_tools_when_brain_unwired(self) -> None:
        engine = _engine(factory=None)

        invoker = engine._make_tool_invoker(
            _elevated_identity(),
            project_id=_PROJECT_ID,
        )

        assert invoker is not None
        names = [d.name for d in invoker.get_permitted_definitions()]
        for tool_name in _BRAIN_TOOL_NAMES:
            assert tool_name not in names


class TestAgentEngineResumeBrainToolWiring:
    """The resume path threads ``task.project`` so brain tools survive resume.

    ``_build_resume_runtime`` builds its own tool invoker (it does not reuse
    the original run's), so without ``project_id=task.project`` an agent
    resumed from a parked run would silently lose ``write_brain_entry`` /
    ``search_brain``. This guards that wiring directly (the per-task
    ``_make_tool_invoker`` tests above would stay green if the resume call
    dropped the argument).
    """

    def test_resume_runtime_registers_brain_tools_for_project_task(self) -> None:
        engine = _engine(factory=_brain_factory())
        task = _resume_task()

        tool_invoker, _system_prompt = engine._build_resume_runtime(
            _elevated_identity(),
            task,
            task_id=str(task.id),
            effective_autonomy=None,
        )

        assert isinstance(tool_invoker, ToolInvoker)
        names = [d.name for d in tool_invoker.get_permitted_definitions()]
        for tool_name in _BRAIN_TOOL_NAMES:
            assert tool_name in names
