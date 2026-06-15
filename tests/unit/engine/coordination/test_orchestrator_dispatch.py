"""Orchestrator strategy is injected into and applied by the WaveDispatcher."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import CoordinationTopology, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.dispatcher_factory import select_dispatcher
from synthorg.engine.coordination.wave_dispatcher import WaveDispatcher
from synthorg.engine.middleware.models import ProgressLedger
from synthorg.engine.middleware.orchestrator_strategy import (
    NaiveDispatchStrategy,
    create_orchestrator_strategy,
)
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _identity(label: str) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(label),
        name="Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _assignment(task_id: str) -> AgentAssignment:
    return AgentAssignment(
        identity=_identity(f"agent-{task_id}"),
        task=Task(
            id=as_uuid(task_id),
            title=f"Task {task_id}",
            description="A detailed test task description",
            type=TaskType.DEVELOPMENT,
            project="test-project",
            created_by="test-creator",
        ),
        resource_claims=(),
    )


def _group(task_ids: tuple[str, ...]) -> ParallelExecutionGroup:
    return ParallelExecutionGroup(
        group_id=NotBlankStr("wave-0"),
        assignments=tuple(_assignment(t) for t in task_ids),
    )


class _ReverseStrategy:
    """Test strategy that reverses subtask order regardless of progress."""

    @property
    def name(self) -> str:
        return "reverse"

    async def select_subtasks(
        self,
        subtask_ids: tuple[NotBlankStr, ...],
        progress: ProgressLedger | None,
    ) -> tuple[NotBlankStr, ...]:
        return tuple(reversed(subtask_ids))


class TestSelectDispatcherThreadsStrategy:
    def test_centralized_receives_strategy(self) -> None:
        strategy = create_orchestrator_strategy("naive")
        dispatcher = select_dispatcher(
            CoordinationTopology.CENTRALIZED,
            orchestrator_strategy=strategy,
        )
        assert isinstance(dispatcher, WaveDispatcher)
        assert dispatcher._orchestrator_strategy is strategy

    def test_no_strategy_defaults_to_none(self) -> None:
        dispatcher = select_dispatcher(CoordinationTopology.CENTRALIZED)
        assert isinstance(dispatcher, WaveDispatcher)
        assert dispatcher._orchestrator_strategy is None


class TestApplyOrchestratorStrategy:
    async def test_no_strategy_is_identity(self) -> None:
        dispatcher = WaveDispatcher(
            isolation_required=False, topology_label="centralized"
        )
        groups = (_group(("a", "b", "c")),)
        result = await dispatcher._apply_orchestrator_strategy(groups)
        assert result is groups

    async def test_naive_preserves_order(self) -> None:
        dispatcher = WaveDispatcher(
            isolation_required=False,
            topology_label="centralized",
            orchestrator_strategy=NaiveDispatchStrategy(),
        )
        groups = (_group(("a", "b", "c")),)
        result = await dispatcher._apply_orchestrator_strategy(groups)
        ids = tuple(str(a.task.id) for a in result[0].assignments)
        assert ids == (str(as_uuid("a")), str(as_uuid("b")), str(as_uuid("c")))

    async def test_strategy_reorders_assignments(self) -> None:
        dispatcher = WaveDispatcher(
            isolation_required=False,
            topology_label="centralized",
            orchestrator_strategy=_ReverseStrategy(),
        )
        groups = (_group(("a", "b", "c")),)
        result = await dispatcher._apply_orchestrator_strategy(groups)
        ids = tuple(str(a.task.id) for a in result[0].assignments)
        assert ids == (str(as_uuid("c")), str(as_uuid("b")), str(as_uuid("a")))
