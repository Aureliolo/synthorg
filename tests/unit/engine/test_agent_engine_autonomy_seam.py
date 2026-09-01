"""Every dispatch path resolves autonomy from the same owner.

A coordinated wave calls ``AgentEngine.run`` directly, with no autonomy
argument. Before the seam existed the engine ran those agents with none,
so an operator who configured ``autonomy_tiered`` output scanning got the
weakest tier on every team agent and was never told.
"""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.providers.protocol import CompletionProvider
from tests._shared import as_uuid, engine_with, mock_of

pytestmark = pytest.mark.unit


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("agent-a"),
        name="Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


def _task() -> Task:
    return Task(
        id=as_uuid("task-a"),
        title="Task",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="manager",
        status=TaskStatus.ASSIGNED,
        assigned_to=NotBlankStr(str(as_uuid("agent-a"))),
    )


def _autonomy() -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=AutonomyLevel.SEMI,
        auto_approve_actions=frozenset({"code:read"}),
        human_approval_actions=frozenset({"deploy:production"}),
        security_agent=False,
    )


class TestAutonomySeam:
    async def test_unbound_seam_resolves_to_none(self) -> None:
        """An engine nobody bound a resolver to runs degraded, as before."""
        engine = engine_with(mock_of[CompletionProvider]())

        resolved = await engine._effective_autonomy_for(
            _identity(), task_id="task-a", project_id=NotBlankStr("proj-001")
        )

        assert resolved is None

    async def test_bound_seam_is_asked_for_the_task_and_project(self) -> None:
        """The engine asks the one owner, naming the run it is asking about."""
        engine = engine_with(mock_of[CompletionProvider]())
        seen: list[tuple[str, str, str | None]] = []
        expected = _autonomy()

        async def _resolution(
            identity: AgentIdentity,
            *,
            task_id: str,
            project_id: NotBlankStr | None = None,
        ) -> EffectiveAutonomy:
            seen.append((str(identity.id), task_id, project_id))
            return expected

        engine.set_autonomy_resolution(_resolution)

        resolved = await engine._effective_autonomy_for(
            _identity(), task_id="task-a", project_id=NotBlankStr("proj-001")
        )

        assert resolved is expected
        assert seen == [(str(as_uuid("agent-a")), "task-a", "proj-001")]

    async def test_run_resolves_when_the_caller_supplied_none(self) -> None:
        """The coordinated path (no autonomy argument) still gets an answer."""
        engine = engine_with(mock_of[CompletionProvider]())
        expected = _autonomy()
        asked: list[tuple[str, str, str | None]] = []

        async def _resolution(
            identity: AgentIdentity,
            *,
            task_id: str,
            project_id: NotBlankStr | None = None,
        ) -> EffectiveAutonomy:
            asked.append((str(identity.id), task_id, project_id))
            return expected

        engine.set_autonomy_resolution(_resolution)
        result = await engine.run(identity=_identity(), task=_task())

        # Every input, not only the task: a run that asked with no project
        # would silently drop the project-mode override, and one that asked
        # about a different agent would resolve somebody else's autonomy.
        # The run fails at the provider (a bare mock), which is fine: the
        # question is whether the seam was consulted before dispatch, and it
        # must have been consulted BEFORE that failure rather than skipped by
        # it, which is what the ERROR outcome alongside the call pins.
        assert asked == [(str(as_uuid("agent-a")), str(as_uuid("task-a")), "proj-001")]
        assert result.termination_reason is TerminationReason.ERROR
