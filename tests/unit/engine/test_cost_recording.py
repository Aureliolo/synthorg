"""Tests for per-turn cost recording."""

from typing import TYPE_CHECKING, override

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.tracker import CostTracker
from synthorg.engine.cost_recording import (
    record_execution_costs,
    resolve_tracker_currency,
)
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason

if TYPE_CHECKING:
    from synthorg.budget.cost_record import CostRecord
    from synthorg.core.agent import AgentIdentity


def _turn(
    *,
    turn_number: int = 1,
    cost: float = 0.01,
    input_tokens: int = 100,
    output_tokens: int = 50,
    call_category: LLMCallCategory | None = None,
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
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


class _FakeTracker(CostTracker):
    """In-memory tracker that records submitted CostRecords.

    Subclasses the real ``CostTracker`` (bypassing its ``__init__``) so the
    runtime-resolved ``tracker: CostTracker`` boundary accepts it.
    """

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.records: list[CostRecord] = []
        self._fail_on = fail_on
        self._call_count = 0

    @override
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
            _turn(turn_number=1, cost=0.01, input_tokens=100, output_tokens=50),
            _turn(turn_number=2, cost=0.02, input_tokens=200, output_tokens=100),
        )
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert len(tracker.records) == 2
        assert tracker.records[0].cost == 0.01
        assert tracker.records[1].cost == 0.02

    async def test_skips_zero_cost_zero_tokens(self) -> None:
        turns = (_turn(cost=0.0, input_tokens=0, output_tokens=0),)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert len(tracker.records) == 0

    async def test_records_free_tier_turn(self) -> None:
        """Zero cost but nonzero tokens should still be recorded."""
        turns = (_turn(cost=0.0, input_tokens=100, output_tokens=50),)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert len(tracker.records) == 1
        assert tracker.records[0].cost == 0.0
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
            tracker=tracker,
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
            tracker=tracker,
        )
        # Second turn still recorded despite first failure
        assert len(tracker.records) == 1

    async def test_memory_error_propagates(self) -> None:
        """MemoryError in tracker.record() propagates unconditionally."""

        class _MemoryErrorTracker(CostTracker):
            def __init__(self) -> None:
                """Bypass the real CostTracker init."""

            @override
            async def record(self, cost_record: CostRecord) -> None:
                del cost_record
                raise MemoryError

        with pytest.raises(MemoryError):
            await record_execution_costs(
                _result((_turn(),)),
                _identity(),
                "agent-1",
                "task-1",
                tracker=_MemoryErrorTracker(),
            )

    async def test_recursion_error_propagates(self) -> None:
        """RecursionError in tracker.record() propagates unconditionally."""

        class _RecursionErrorTracker(CostTracker):
            def __init__(self) -> None:
                """Bypass the real CostTracker init."""

            @override
            async def record(self, cost_record: CostRecord) -> None:
                del cost_record
                raise RecursionError

        with pytest.raises(RecursionError):
            await record_execution_costs(
                _result((_turn(),)),
                _identity(),
                "agent-1",
                "task-1",
                tracker=_RecursionErrorTracker(),
            )


@pytest.mark.unit
class TestAnalyticsFieldPropagation:
    """New per-call analytics fields are propagated from TurnRecord to CostRecord."""

    def _turn_with_analytics(
        self,
        *,
        latency_ms: float | None = None,
        cache_hit: bool | None = None,
        retry_count: int | None = None,
        retry_reason: str | None = None,
    ) -> TurnRecord:
        from synthorg.providers.enums import FinishReason

        return TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            finish_reason=FinishReason.STOP,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            retry_count=retry_count,
            retry_reason=retry_reason,
        )

    async def test_latency_ms_propagated(self) -> None:
        """latency_ms from TurnRecord lands in CostRecord."""
        turn = self._turn_with_analytics(latency_ms=250.5)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result((turn,)),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert tracker.records[0].latency_ms == 250.5

    async def test_cache_hit_propagated(self) -> None:
        turn = self._turn_with_analytics(cache_hit=True)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result((turn,)),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert tracker.records[0].cache_hit is True

    async def test_retry_count_propagated(self) -> None:
        turn = self._turn_with_analytics(retry_count=2)
        tracker = _FakeTracker()
        await record_execution_costs(
            _result((turn,)),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert tracker.records[0].retry_count == 2

    async def test_retry_reason_propagated(self) -> None:
        turn = self._turn_with_analytics(retry_count=2, retry_reason="RateLimitError")
        tracker = _FakeTracker()
        await record_execution_costs(
            _result((turn,)),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert tracker.records[0].retry_reason == "RateLimitError"

    async def test_finish_reason_and_success_propagated(self) -> None:
        from synthorg.providers.enums import FinishReason

        turn = TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            finish_reason=FinishReason.ERROR,
        )
        tracker = _FakeTracker()
        await record_execution_costs(
            _result((turn,)),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert tracker.records[0].finish_reason == FinishReason.ERROR
        assert tracker.records[0].success is False

    async def test_none_analytics_fields_propagated_as_none(self) -> None:
        """When analytics fields are None on TurnRecord, CostRecord gets None."""
        turn = _turn()
        tracker = _FakeTracker()
        await record_execution_costs(
            _result((turn,)),
            _identity(),
            "agent-1",
            "task-1",
            tracker=tracker,
        )
        assert tracker.records[0].latency_ms is None
        assert tracker.records[0].cache_hit is None
        assert tracker.records[0].retry_count is None
        assert tracker.records[0].retry_reason is None


@pytest.mark.unit
class TestProjectIdPropagation:
    """Tests for project_id propagation through cost recording."""

    @pytest.mark.parametrize(
        ("turns", "project_id", "expected_count", "expected_ids"),
        [
            pytest.param(
                (_turn(),),
                "proj-100",
                1,
                ("proj-100",),
                id="single-turn-with-project",
            ),
            pytest.param(
                (_turn(),),
                None,
                1,
                (None,),
                id="single-turn-none-by-default",
            ),
            pytest.param(
                (
                    _turn(turn_number=1, cost=0.01),
                    _turn(turn_number=2, cost=0.02),
                ),
                "proj-200",
                2,
                ("proj-200", "proj-200"),
                id="multi-turn-all-tagged",
            ),
        ],
    )
    async def test_project_id_propagation(
        self,
        turns: tuple[TurnRecord, ...],
        project_id: str | None,
        expected_count: int,
        expected_ids: tuple[str | None, ...],
    ) -> None:
        tracker = _FakeTracker()
        kwargs: dict[str, object] = {
            "tracker": tracker,
        }
        if project_id is not None:
            kwargs["project_id"] = project_id
        await record_execution_costs(
            _result(turns),
            _identity(),
            "agent-1",
            "task-1",
            **kwargs,  # type: ignore[arg-type]
        )
        assert len(tracker.records) == expected_count
        assert tuple(r.project_id for r in tracker.records) == expected_ids


@pytest.mark.unit
class TestResolveTrackerCurrency:
    """``resolve_tracker_currency`` resolves the ISO 4217 code or falls back."""

    def test_none_tracker_returns_default(self) -> None:
        assert resolve_tracker_currency(None) == DEFAULT_CURRENCY

    def test_tracker_without_budget_config_returns_default(self) -> None:
        """A tracker whose ``budget_config`` is ``None`` falls back."""
        from synthorg.budget.tracker import CostTracker

        tracker = CostTracker()
        assert tracker.budget_config is None
        assert resolve_tracker_currency(tracker) == DEFAULT_CURRENCY

    def test_tracker_with_budget_config_returns_configured_currency(self) -> None:
        """Configured ``budget_config.currency`` flows through unchanged."""
        from synthorg.budget.config import BudgetConfig
        from synthorg.budget.tracker import CostTracker

        tracker = CostTracker(
            budget_config=BudgetConfig(total_monthly=100.0, currency="EUR"),
        )
        assert resolve_tracker_currency(tracker) == "EUR"
