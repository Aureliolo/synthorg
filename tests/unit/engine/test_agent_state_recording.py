"""The engine records an agent's live runtime state as a run progresses.

``AgentRuntimeState`` shipped with a model, a repository protocol and a table
in both backends, and nothing wrote it. These tests pin the writers: one per
turn through the loop's own progress hook, and one at the end of the dispatch
marking the agent idle. Both are observation, so neither may fail the run
that produced the state.
"""

import asyncio
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
    mark_agent_running,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TurnProgress
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.providers.models import TokenUsage
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
_EUR = CurrencyCode("EUR")


class _PublishFailedError(RuntimeError):
    """Stands in for a listener's own fault, whatever its cause."""


def _context(
    identity: AgentIdentity,
    task: Task,
    *,
    turn: int,
    cost: float,
    execution_id: str | None = None,
) -> AgentContext:
    """Build a run context carrying real spend and turn count.

    Returns:
        The context a live run would hold at that point.
    """
    ctx = AgentContext.from_identity(identity, task=task)
    update: dict[str, object] = {
        "turn_count": turn,
        "accumulated_cost": TokenUsage(
            input_tokens=10,
            output_tokens=5,
            cost=cost,
        ),
    }
    if execution_id is not None:
        update["execution_id"] = execution_id
    return ctx.model_copy(update=update)


def _progress(
    identity: AgentIdentity,
    task: Task,
    *,
    turn: int,
    cost: float,
) -> TurnProgress:
    """Build a turn report whose context carries real spend and turn count.

    Returns:
        The report the loop hands its observer.
    """
    return TurnProgress(
        turn,
        ("write_file",),
        _context(identity, task, turn=turn, cost=cost),
    )


def _claiming_repository(
    saved: list[AgentRuntimeState],
    *,
    claimed: bool = True,
) -> AgentStateRepository:
    """A repository recording the claim every running write now makes.

    The running writes are a compare-and-set on execution ownership rather
    than a plain upsert, so a fake wired to ``save`` alone observes none of
    them.

    Args:
        saved: Collects each state the writer offered.
        claimed: What the store answers, i.e. whether this execution holds
            the row.

    Returns:
        The recording repository.
    """

    async def _claim(state: AgentRuntimeState, **_: object) -> bool:
        saved.append(state)
        return claimed

    repository: AgentStateRepository = mock_of[AgentStateRepository](
        save=AsyncMock(side_effect=saved.append),
        save_if_execution=AsyncMock(side_effect=_claim),
    )
    return repository


class TestTheLiveStateFollowsTheRun:
    async def test_a_turn_records_the_runs_own_numbers(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        saved: list[AgentRuntimeState] = []
        repository = _claiming_repository(saved)
        observer = make_runtime_state_observer(
            repository_provider=lambda: repository,
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        await observer(
            _progress(
                sample_agent,
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
        sample_agent: AgentIdentity,
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
                sample_agent,
                sample_task_with_criteria,
                turn=1,
                cost=0.0,
            )
        )

    async def test_a_storage_fault_never_reaches_the_run(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Watching a run must not be able to fail it."""
        repository = mock_of[AgentStateRepository](
            save_if_execution=AsyncMock(side_effect=RuntimeError("state store down")),
        )
        observer = make_runtime_state_observer(
            repository_provider=lambda: repository,
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        await observer(
            _progress(
                sample_agent,
                sample_task_with_criteria,
                turn=1,
                cost=0.0,
            )
        )


class TestTheAgentStopsReadingAsBusy:
    async def test_idle_is_recorded_when_the_dispatch_ends(self) -> None:
        saved: list[AgentRuntimeState] = []

        async def _write(state: AgentRuntimeState, **_: object) -> bool:
            saved.append(state)
            return True

        repository = mock_of[AgentStateRepository](
            save_if_execution=AsyncMock(side_effect=_write),
        )

        await mark_agent_idle(
            repository_provider=lambda: repository,
            agent_id="agent-1",
            execution_id="exec-1",
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
            save_if_execution=AsyncMock(side_effect=RuntimeError("store down")),
        )

        await mark_agent_idle(
            repository_provider=lambda: repository,
            agent_id="agent-1",
            execution_id="exec-1",
            currency=_EUR,
        )

    async def test_the_clear_names_the_execution_it_believes_holds_the_row(
        self,
    ) -> None:
        """The guard is the repository's to apply, so the run must hand it over.

        Deciding here and writing unconditionally is what left a gap for a
        sibling to claim the agent in; the run's job is now to say which
        execution it thinks owns the row, and the write refuses if that has
        stopped being true. Passing the wrong id, or none, would re-open the
        window silently, since the write would still succeed.
        """
        repository = mock_of[AgentStateRepository](
            save_if_execution=AsyncMock(return_value=True),
        )

        await mark_agent_idle(
            repository_provider=lambda: repository,
            agent_id="agent-1",
            execution_id="exec-mine",
            currency=_EUR,
        )

        assert repository.save_if_execution.await_args is not None
        assert (
            repository.save_if_execution.await_args.kwargs["expected_execution_id"]
            == "exec-mine"
        )

    async def test_a_declined_write_is_not_an_error(self) -> None:
        """A sibling holding the row is the guard working, not a fault.

        The repository answers ``False`` and the dispatch that just finished
        carries on unwound: the sibling is still running, so the row it owns
        is the correct one to leave in place.
        """
        repository = mock_of[AgentStateRepository](
            save_if_execution=AsyncMock(return_value=False),
        )

        await mark_agent_idle(
            repository_provider=lambda: repository,
            agent_id="agent-1",
            execution_id="exec-mine",
            currency=_EUR,
        )


class TestARunIsVisibleBeforeItsFirstTurnEnds:
    """A turn that finishes the run returns instead of reporting.

    Both loops notify the turn observer only when a turn CONTINUES, so a
    single-turn dispatch never reports at all and a longer one is invisible
    until its first turn ends, which is one whole LLM call. Everything
    reading live state falls back to recorded frames in that window, and for
    a run in flight those read zero.
    """

    async def test_a_row_is_written_at_dispatch(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        saved: list[AgentRuntimeState] = []
        repository = _claiming_repository(saved)

        await mark_agent_running(
            repository_provider=lambda: repository,
            context=_context(
                sample_agent,
                sample_task_with_criteria,
                turn=0,
                cost=0.0,
            ),
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        assert len(saved) == 1
        assert saved[0].status is ExecutionStatus.EXECUTING
        assert saved[0].turn_count == 0

    async def test_the_running_write_asserts_its_own_execution(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The row is per agent, and an agent can hold two dispatches.

        A plain upsert made ownership last-write-wins, so two overlapping
        runs on one agent alternated the row every turn and the live view
        flipped between them. The write is a compare-and-set on the writer's
        OWN execution, so the claim is what the store arbitrates.
        """
        repository = mock_of[AgentStateRepository](
            save_if_execution=AsyncMock(return_value=True),
        )

        await mark_agent_running(
            repository_provider=lambda: repository,
            context=_context(
                sample_agent,
                sample_task_with_criteria,
                turn=0,
                cost=0.0,
                execution_id="exec-a",
            ),
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        assert repository.save_if_execution.await_args is not None
        assert (
            repository.save_if_execution.await_args.kwargs["expected_execution_id"]
            == "exec-a"
        )

    async def test_a_sibling_holding_the_row_is_not_overwritten(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A refused claim is not a failure, and must not fail the run.

        The second dispatch keeps working with no live row of its own; the
        cockpit reads its recorded frames, which is what it already does for
        an agent that has none. The alternative -- taking the row anyway --
        is what made the first dispatch disappear from the live view.
        """
        repository = mock_of[AgentStateRepository](
            save_if_execution=AsyncMock(return_value=False),
        )
        observer = make_runtime_state_observer(
            repository_provider=lambda: repository,
            currency=_EUR,
            clock=FakeClock(start=_NOW),
        )

        await observer(
            _progress(
                sample_agent,
                sample_task_with_criteria,
                turn=3,
                cost=0.5,
            )
        )

        assert repository.save_if_execution.await_count == 1
        assert repository.save.await_count == 0

    async def test_no_store_is_a_noop(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        await mark_agent_running(
            repository_provider=lambda: None,
            context=_context(
                sample_agent,
                sample_task_with_criteria,
                turn=0,
                cost=0.0,
            ),
            currency=_EUR,
        )


class TestOneReportReachesEveryListener:
    async def test_every_wired_observer_sees_the_turn(
        self,
        sample_agent: AgentIdentity,
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
                sample_agent,
                sample_task_with_criteria,
                turn=4,
                cost=0.0,
            )
        )

        assert seen == ["first:4", "second:4"]

    def test_nothing_wired_is_no_observer(self) -> None:
        """``None`` is what disables the hook, so composing nothing returns it."""
        assert compose_turn_observers(None, None) is None

    async def test_a_lone_failing_observer_is_guarded_too(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The guard is a promise about the run, not about the other watchers.

        Handing a single observer back unwrapped is the cheaper composition of
        nothing, and it silently exempts the common case from the one
        guarantee this function makes: that watching a run cannot fail it.
        """

        async def _only(_report: TurnProgress) -> None:
            raise _PublishFailedError

        composed = compose_turn_observers(None, _only)
        assert composed is not None

        await composed(
            _progress(
                sample_agent,
                sample_task_with_criteria,
                turn=4,
                cost=0.0,
            )
        )

    async def test_one_failing_listener_does_not_silence_the_others(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """They watch different things for different people.

        Run as an unguarded chain, a fault in the stream observer would cost
        the live-activity row its turn, and the loop's own wrapper would
        swallow the exception, so the cockpit would go stale with nothing
        said. A fault in either says nothing about the other.
        """
        seen: list[str] = []

        async def _broken(_report: TurnProgress) -> None:
            raise _PublishFailedError

        async def _healthy(progress: TurnProgress) -> None:
            seen.append(f"healthy:{progress.turn_number}")

        composed = compose_turn_observers(_broken, _healthy)
        assert composed is not None
        await composed(
            _progress(
                sample_agent,
                sample_task_with_criteria,
                turn=4,
                cost=0.0,
            )
        )

        assert seen == ["healthy:4"]

    async def test_cancellation_stops_every_listener(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """The run is being torn down, so continuing to watch it is wrong."""
        seen: list[str] = []

        async def _cancelled(_report: TurnProgress) -> None:
            raise asyncio.CancelledError

        async def _later(progress: TurnProgress) -> None:
            seen.append(f"later:{progress.turn_number}")

        composed = compose_turn_observers(_cancelled, _later)
        assert composed is not None
        with pytest.raises(asyncio.CancelledError):
            await composed(
                _progress(
                    sample_agent,
                    sample_task_with_criteria,
                    turn=4,
                    cost=0.0,
                )
            )

        assert seen == []


def test_the_idle_state_carries_the_operators_currency() -> None:
    """Stored even at a zero balance, so the row always has an unambiguous unit."""
    state = AgentRuntimeState.idle(
        NotBlankStr("agent-1"),
        currency=CurrencyCode("USD"),
        clock=FakeClock(start=_NOW),
    )
    assert state.currency == "USD"
