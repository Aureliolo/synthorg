"""Tests for per-turn cost recording."""

from typing import TYPE_CHECKING

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.engine.cost_recording import record_execution_costs
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.providers.enums import FinishReason

if TYPE_CHECKING:
    from synthorg.budget.cost_record import CostRecord
    from synthorg.core.agent import AgentIdentity


def _turn(
    *,
    turn_number: int = 1,
    cost_usd: float = 0.01,
    input_tokens: int = 100,
    output_tokens: int = 50,
    call_category: LLMCallCategory | None = None,
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        finish_reason=FinishReason.STOP,
        call_category=call_category,
    )


def _result(turns: tuple[TurnRecord, ...]) -> ExecutionResult:
    """Minimal ExecutionResult wrapping the given turns."""
    from synthorg.engine.context import AgentContext

    ctx = AgentContext.from_identity(_identity())
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
    )


def _identity() -> AgentIdentity:
    from datetime import date
    from uuid import uuid4

    from synthorg.core.agent import AgentIdentity, ModelConfig

    return AgentIdentity(
        id=uuid4(),
        name="Cost Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
    )


class _FakeTracker:
    """In-memory tracker that records submitted CostRecords."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.records: list[CostRecord] = []
        self._fail_on = fail_on
        self._call_count = 0

    async def record(self, cost_record: CostRecord) -> None:
        self._call_count += 1
        if self._fail_on is not None and self._call_count == self._fail_on:
            msg = "injected failure"
            raise RuntimeError(msg)
        self.records.append(cost_record)


@pytest.mark.unit
class TestRecordExecutionCosts:
    """record_execution_costs function."""

    async def test_no_tracker_is_noop(self) -> None:
        result = _result((_turn(),))
        await record_execution_costs(
            result,
            _identity(),
            "agent-1",
            "task-1",
            tracker=None,
        )

    async def test_records_each_turn(self) -> None:
        turns = (
            _turn(turn_number=1, cost_usd=0.01, input_tokens=100, output_tokens=50),
            _turn(turn_number=2, cost_usd=0.02, input_tokens=200, output_tokens=100),
        )
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,  # type: ignore[arg-type]
        )
        assert len(tracker.records) == 2
        assert tracker.records[0].cost_usd == 0.01
        assert tracker.records[1].cost_usd == 0.02

    async def test_skips_zero_cost_zero_tokens(self) -> None:
        turns = (_turn(cost_usd=0.0, input_tokens=0, output_tokens=0),)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,  # type: ignore[arg-type]
        )
        assert len(tracker.records) == 0

    async def test_records_free_tier_turn(self) -> None:
        """Zero cost but nonzero tokens should still be recorded."""
        turns = (_turn(cost_usd=0.0, input_tokens=100, output_tokens=50),)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,  # type: ignore[arg-type]
        )
        assert len(tracker.records) == 1
        assert tracker.records[0].cost_usd == 0.0
        assert tracker.records[0].input_tokens == 100

    async def test_call_category_propagated(self) -> None:
        turns = (
            _turn(call_category=LLMCallCategory.PRODUCTIVE),
            _turn(turn_number=2, call_category=LLMCallCategory.SYSTEM),
        )
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,  # type: ignore[arg-type]
        )
        assert tracker.records[0].call_category == LLMCallCategory.PRODUCTIVE
        assert tracker.records[1].call_category == LLMCallCategory.SYSTEM

    async def test_regular_exception_swallowed(self) -> None:
        """Regular exceptions in tracker.record() are logged, not raised."""
        turns = (
            _turn(turn_number=1),
            _turn(turn_number=2),
        )
        tracker = _FakeTracker(fail_on=1)
        # Should not raise
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,  # type: ignore[arg-type]
        )
        # Second turn still recorded despite first failure
        assert len(tracker.records) == 1

    async def test_memory_error_propagates(self) -> None:
        """MemoryError in tracker.record() propagates unconditionally."""

        class _MemoryErrorTracker:
            async def record(self, _: CostRecord) -> None:
                raise MemoryError

        with pytest.raises(MemoryError):
            await record_execution_costs(
                _result((_turn(),)),
                _identity(),
                "agent-1",
                "task-1",
                tracker=_MemoryErrorTracker(),  # type: ignore[arg-type]
            )

    async def test_recursion_error_propagates(self) -> None:
        """RecursionError in tracker.record() propagates unconditionally."""

        class _RecursionErrorTracker:
            async def record(self, _: CostRecord) -> None:
                raise RecursionError

        with pytest.raises(RecursionError):
            await record_execution_costs(
                _result((_turn(),)),
                _identity(),
                "agent-1",
                "task-1",
                tracker=_RecursionErrorTracker(),  # type: ignore[arg-type]
            )
