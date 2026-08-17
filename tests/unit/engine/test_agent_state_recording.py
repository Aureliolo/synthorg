"""The engine records an agent's live runtime state as a run progresses.

``AgentRuntimeState`` shipped with a model, a repository protocol and a table
in both backends, and nothing wrote it. These tests pin the writers: one per
turn through the loop's own progress hook, and one at the end of the dispatch
marking the agent idle. Both are observation, so neither may fail the run
that produced the state.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import CurrencyCode
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
from synthorg.engine.agent_state_recording import (
    compose_turn_observers,
    make_runtime_state_observer,
    mark_agent_idle,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TurnProgress
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.providers.models import TokenUsage
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
_EUR = CurrencyCode("EUR")


def _progress(
    identity: AgentIdentity,
    task: Task,
    *,
    turn: int,
    cost: float,
) -> TurnProgress:
    """Build a turn report whose context carries real spend and turn count."""
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.model_copy(
        update={
            "turn_count": turn,
            "accumulated_cost": TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cost=cost,
            ),
        },
    )
    return TurnProgress(turn, ("write_file",), ctx)


class TestTheLiveStateFollowsTheRun:
    async def test_a_turn_records_the_runs_own_numbers(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        saved: list[AgentRuntimeState] = []
        repository = mock_of[AgentStateRepository](
            save=AsyncMock(side_effect=saved.append),
        )
        observer = make_runtime_state_observer(
            repository_provider=lambda: repository,
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        await observer(
            _progress(
                sample_agent_with_personality,
                sample_task_with_criteria,
                turn=12,
                cost=0.75,
            )
        )

        assert len(saved) == 1
        assert saved[0].status is ExecutionStatus.EXECUTING
        assert saved[0].turn_count == 12
        assert saved[0].accumulated_cost == pytest.approx(0.75)
        assert saved[0].task_id == str(sample_task_with_criteria.id)
        assert saved[0].last_activity_at == _NOW

    async def test_no_store_yet_is_not_a_failure(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A run can start before persistence connects; it still has to run."""
        observer = make_runtime_state_observer(
            repository_provider=lambda: None,
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        await observer(
            _progress(
                sample_agent_with_personality,
                sample_task_with_criteria,
                turn=1,
                cost=0.0,
            )
        )

    async def test_a_storage_fault_never_reaches_the_run(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Watching a run must not be able to fail it."""
        repository = mock_of[AgentStateRepository](
            save=AsyncMock(side_effect=RuntimeError("state store down")),
        )
        observer = make_runtime_state_observer(
            repository_provider=lambda: repository,
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        await observer(
            _progress(
                sample_agent_with_personality,
                sample_task_with_criteria,
                turn=1,
                cost=0.0,
            )
        )


class TestTheAgentStopsReadingAsBusy:
    async def test_idle_is_recorded_when_the_dispatch_ends(self) -> None:
        saved: list[AgentRuntimeState] = []
        repository = mock_of[AgentStateRepository](
            save=AsyncMock(side_effect=saved.append),
        )

        await mark_agent_idle(
            repository_provider=lambda: repository,
            agent_id="agent-1",
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        assert len(saved) == 1
        assert saved[0].status is ExecutionStatus.IDLE
        assert saved[0].agent_id == "agent-1"
        assert saved[0].task_id is None
        assert saved[0].execution_id is None

    async def test_a_storage_fault_is_not_raised_at_teardown(self) -> None:
        """The idle write runs in a finally; raising there would mask the run's
        own outcome."""
        repository = mock_of[AgentStateRepository](
            save=AsyncMock(side_effect=RuntimeError("state store down")),
        )

        await mark_agent_idle(
            repository_provider=lambda: repository,
            agent_id="agent-1",
            currency=_EUR,
        )


class TestOneReportReachesEveryListener:
    async def test_every_wired_observer_sees_the_turn(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        seen: list[str] = []

        async def _first(progress: TurnProgress) -> None:
            seen.append(f"first:{progress.turn_number}")

        async def _second(progress: TurnProgress) -> None:
            seen.append(f"second:{progress.turn_number}")

        composed = compose_turn_observers(_first, None, _second)
        assert composed is not None
        await composed(
            _progress(
                sample_agent_with_personality,
                sample_task_with_criteria,
                turn=4,
                cost=0.0,
            )
        )

        assert seen == ["first:4", "second:4"]

    def test_nothing_wired_is_no_observer(self) -> None:
        """``None`` is what disables the hook, so composing nothing returns it."""
        assert compose_turn_observers(None, None) is None

    def test_one_observer_is_passed_through_unwrapped(self) -> None:
        async def _only(_progress: TurnProgress) -> None:
            return

        assert compose_turn_observers(None, _only) is _only


def test_the_idle_state_carries_the_operators_currency() -> None:
    """Stored even at a zero balance, so the row always has an unambiguous unit."""
    state = AgentRuntimeState.idle(
        NotBlankStr("agent-1"),
        currency=CurrencyCode("USD"),
        clock=FakeClock(start=_NOW),
    )
    assert state.currency == "USD"
