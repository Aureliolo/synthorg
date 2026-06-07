"""Tests for AgentRuntimeState model."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from pydantic import AwareDatetime

from synthorg.core.enums import ExecutionStatus
from synthorg.engine.agent_state import AgentRuntimeState
from tests._shared import as_uuid, sid

if TYPE_CHECKING:
    from synthorg.engine.context import AgentContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _make_executing_state(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    execution_id: str = "exec-001",
    task_id: str | None = "task-001",
    status: ExecutionStatus = ExecutionStatus.EXECUTING,
    turn_count: int = 3,
    accumulated_cost: float = 0.05,
    last_activity_at: AwareDatetime = _NOW,
    started_at: AwareDatetime = _NOW,
) -> AgentRuntimeState:
    return AgentRuntimeState(
        agent_id=agent_id,
        execution_id=execution_id,
        task_id=task_id,
        status=status,
        turn_count=turn_count,
        accumulated_cost=accumulated_cost,
        currency="EUR",
        last_activity_at=last_activity_at,
        started_at=started_at,
    )


def _make_context(
    *,
    agent_id: str = "agent-ctx",
    task_id: str | None = "task-ctx",
    turn_count: int = 5,
    cost: float = 0.10,
) -> AgentContext:
    """Build a minimal AgentContext for testing from_context."""
    from datetime import date
    from uuid import UUID

    from synthorg.core.agent import AgentIdentity, ModelConfig
    from synthorg.core.enums import TaskType
    from synthorg.core.task import Task
    from synthorg.engine.context import AgentContext
    from synthorg.engine.task_execution import TaskExecution
    from synthorg.providers.models import ZERO_TOKEN_USAGE, TokenUsage

    identity = AgentIdentity(
        id=UUID(int=0) if agent_id == "agent-ctx" else uuid4(),
        name=agent_id,
        role="engineer",
        department="engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )

    task_execution = None
    if task_id is not None:
        task = Task(
            id=as_uuid(task_id),
            title="Test task",
            description="A test task",
            type=TaskType.DEVELOPMENT,
            project="test-project",
            created_by=str(identity.id),
        )
        task_execution = TaskExecution.from_task(task)

    usage = (
        TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cost=cost,
        )
        if cost > 0
        else ZERO_TOKEN_USAGE
    )

    return AgentContext(
        execution_id=str(uuid4()),
        identity=identity,
        task_execution=task_execution,
        turn_count=turn_count,
        accumulated_cost=usage,
        started_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentRuntimeStateIdle:
    """Tests for the idle() factory."""

    def test_idle_creates_idle_state(self) -> None:
        state = AgentRuntimeState.idle("agent-idle", currency="EUR")
        assert state.agent_id == "agent-idle"
        assert state.status == ExecutionStatus.IDLE
        assert state.execution_id is None
        assert state.task_id is None
        assert state.started_at is None
        assert state.turn_count == 0
        assert state.accumulated_cost == 0.0

    def test_idle_sets_last_activity_at(self) -> None:
        state = AgentRuntimeState.idle("agent-idle", currency="EUR")
        assert state.last_activity_at is not None
        assert state.last_activity_at.tzinfo is not None

    def test_idle_with_blank_agent_id_raises(self) -> None:
        with pytest.raises(ValueError, match="whitespace"):
            AgentRuntimeState.idle("  ", currency="EUR")


@pytest.mark.unit
class TestAgentRuntimeStateFromContext:
    """Tests for the from_context() factory."""

    def test_from_context_executing(self) -> None:
        ctx = _make_context()
        state = AgentRuntimeState.from_context(
            ctx, ExecutionStatus.EXECUTING, currency="EUR"
        )
        assert state.agent_id == str(ctx.identity.id)
        assert state.execution_id == ctx.execution_id
        assert state.status == ExecutionStatus.EXECUTING
        assert state.turn_count == ctx.turn_count
        assert state.accumulated_cost == ctx.accumulated_cost.cost
        assert state.started_at == ctx.started_at

    def test_from_context_paused(self) -> None:
        ctx = _make_context()
        state = AgentRuntimeState.from_context(
            ctx, ExecutionStatus.PAUSED, currency="EUR"
        )
        assert state.status == ExecutionStatus.PAUSED

    def test_from_context_with_task(self) -> None:
        ctx = _make_context(task_id="my-task")
        state = AgentRuntimeState.from_context(
            ctx, ExecutionStatus.EXECUTING, currency="EUR"
        )
        assert state.task_id == sid("my-task")

    def test_from_context_without_task(self) -> None:
        ctx = _make_context(task_id=None)
        state = AgentRuntimeState.from_context(
            ctx, ExecutionStatus.EXECUTING, currency="EUR"
        )
        assert state.task_id is None

    def test_from_context_rejects_idle(self) -> None:
        ctx = _make_context()
        with pytest.raises(ValueError, match="IDLE"):
            AgentRuntimeState.from_context(ctx, ExecutionStatus.IDLE, currency="EUR")

    def test_from_context_with_zero_cost(self) -> None:
        ctx = _make_context(cost=0.0)
        state = AgentRuntimeState.from_context(
            ctx, ExecutionStatus.EXECUTING, currency="EUR"
        )
        assert state.accumulated_cost == 0.0


@pytest.mark.unit
class TestAgentRuntimeStateValidation:
    """Tests for status invariant validation."""

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"execution_id": "e"}, "execution_id must be None"),
            ({"task_id": "t"}, "task_id must be None"),
            ({"started_at": _NOW}, "started_at must be None"),
            ({"turn_count": 1}, "turn_count must be 0"),
            ({"accumulated_cost": 0.01}, r"accumulated_cost must be 0\.0"),
        ],
    )
    def test_idle_single_violation_raises(
        self, kwargs: dict[str, object], match: str
    ) -> None:
        fields = {
            "agent_id": "a",
            "status": ExecutionStatus.IDLE,
            "currency": "EUR",
            "last_activity_at": _NOW,
            **kwargs,
        }
        with pytest.raises(ValueError, match=match):
            AgentRuntimeState.model_validate(fields)

    def test_executing_without_execution_id_raises(self) -> None:
        with pytest.raises(ValueError, match="execution_id is required"):
            AgentRuntimeState(
                agent_id="a",
                status=ExecutionStatus.EXECUTING,
                started_at=_NOW,
                currency="EUR",
                last_activity_at=_NOW,
            )

    def test_executing_without_started_at_raises(self) -> None:
        with pytest.raises(ValueError, match="started_at is required"):
            AgentRuntimeState(
                agent_id="a",
                execution_id="e",
                status=ExecutionStatus.EXECUTING,
                currency="EUR",
                last_activity_at=_NOW,
            )

    def test_paused_without_execution_id_raises(self) -> None:
        with pytest.raises(ValueError, match="execution_id is required"):
            AgentRuntimeState(
                agent_id="a",
                status=ExecutionStatus.PAUSED,
                started_at=_NOW,
                currency="EUR",
                last_activity_at=_NOW,
            )

    def test_paused_without_started_at_raises(self) -> None:
        with pytest.raises(ValueError, match="started_at is required"):
            AgentRuntimeState(
                agent_id="a",
                execution_id="e",
                status=ExecutionStatus.PAUSED,
                currency="EUR",
                last_activity_at=_NOW,
            )

    def test_multiple_idle_violations_reported(self) -> None:
        """Multiple violations appear in a single error message."""
        with pytest.raises(ValueError, match=r"execution_id.*task_id"):
            AgentRuntimeState(
                agent_id="a",
                execution_id="e",
                task_id="t",
                status=ExecutionStatus.IDLE,
                currency="EUR",
                last_activity_at=_NOW,
            )

    def test_negative_turn_count_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            _make_executing_state(turn_count=-1)

    def test_negative_cost_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            _make_executing_state(accumulated_cost=-0.01)

    def test_blank_agent_id_raises(self) -> None:
        with pytest.raises(ValueError, match="whitespace"):
            _make_executing_state(agent_id="  ")


@pytest.mark.unit
class TestAgentRuntimeStateImmutability:
    """Tests for frozen model behavior and serialization."""

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        state = _make_executing_state()
        with pytest.raises(ValidationError):
            state.turn_count = 99  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        state = _make_executing_state()
        data = state.model_dump(mode="json")
        restored = AgentRuntimeState.model_validate(data)
        assert restored == state

    def test_json_roundtrip_idle(self) -> None:
        state = AgentRuntimeState.idle("agent-rt", currency="EUR")
        data = state.model_dump(mode="json")
        restored = AgentRuntimeState.model_validate(data)
        assert restored == state
        assert restored.status == ExecutionStatus.IDLE
