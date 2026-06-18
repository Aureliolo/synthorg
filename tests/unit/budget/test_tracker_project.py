"""Unit tests for CostTracker project-level queries."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.project_cost_aggregate import ProjectCostAggregate
from synthorg.budget.tracker import CostTracker

from .conftest import make_cost_record


def _make_project_record(  # noqa: PLR0913
    *,
    project_id: str = "proj-1",
    agent_id: str = "alice",
    task_id: str = "task-001",
    cost: float = 0.05,
    currency: str = "USD",
    timestamp: datetime | None = None,
) -> CostRecord:
    """Build a CostRecord with project_id set."""
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        provider="test-provider",
        model="test-model-001",
        input_tokens=1000,
        output_tokens=500,
        cost=cost,
        currency=currency,
        timestamp=timestamp or datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
    )


@pytest.mark.unit
class TestGetProjectCost:
    """Tests for CostTracker.get_project_cost()."""

    async def test_empty_tracker_returns_zero(self, cost_tracker: CostTracker) -> None:
        result = await cost_tracker.get_project_cost("proj-1")
        assert result == 0.0

    async def test_single_project_record(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(_make_project_record(cost=0.10))
        result = await cost_tracker.get_project_cost("proj-1")
        assert result == pytest.approx(0.10)

    async def test_filters_by_project(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(_make_project_record(project_id="proj-1", cost=0.10))
        await cost_tracker.record(_make_project_record(project_id="proj-2", cost=0.20))
        await cost_tracker.record(_make_project_record(project_id="proj-1", cost=0.30))

        assert await cost_tracker.get_project_cost("proj-1") == pytest.approx(0.40)
        assert await cost_tracker.get_project_cost("proj-2") == pytest.approx(0.20)

    async def test_ignores_records_without_project_id(
        self, cost_tracker: CostTracker
    ) -> None:
        await cost_tracker.record(_make_project_record(project_id="proj-1", cost=0.10))
        # Record without project_id
        await cost_tracker.record(make_cost_record(cost=0.50))

        assert await cost_tracker.get_project_cost("proj-1") == pytest.approx(0.10)

    async def test_time_filtered(self, cost_tracker: CostTracker) -> None:
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        await cost_tracker.record(
            _make_project_record(
                project_id="proj-1",
                cost=0.10,
                timestamp=base,
            )
        )
        await cost_tracker.record(
            _make_project_record(
                project_id="proj-1",
                cost=0.20,
                timestamp=base + timedelta(hours=2),
            )
        )

        result = await cost_tracker.get_project_cost(
            "proj-1",
            start=base + timedelta(hours=1),
        )
        assert result == pytest.approx(0.20)

    async def test_nonexistent_project_returns_zero(
        self, cost_tracker: CostTracker
    ) -> None:
        await cost_tracker.record(_make_project_record(project_id="proj-1", cost=0.10))
        assert await cost_tracker.get_project_cost("proj-999") == 0.0


@pytest.mark.unit
class TestGetProjectRecords:
    """Tests for CostTracker.get_project_records()."""

    async def test_returns_matching_records(self, cost_tracker: CostTracker) -> None:
        r1 = _make_project_record(project_id="proj-1", cost=0.10)
        r2 = _make_project_record(project_id="proj-2", cost=0.20)
        r3 = _make_project_record(project_id="proj-1", cost=0.30)
        await cost_tracker.record(r1)
        await cost_tracker.record(r2)
        await cost_tracker.record(r3)

        records = await cost_tracker.get_project_records("proj-1")
        assert len(records) == 2
        costs = sorted(r.cost for r in records)
        assert costs == pytest.approx([0.10, 0.30])

    async def test_empty_for_unknown_project(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(_make_project_record(project_id="proj-1"))
        records = await cost_tracker.get_project_records("proj-999")
        assert records == ()

    async def test_time_filtered(self, cost_tracker: CostTracker) -> None:
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        await cost_tracker.record(
            _make_project_record(
                project_id="proj-1",
                cost=0.10,
                timestamp=base,
            )
        )
        await cost_tracker.record(
            _make_project_record(
                project_id="proj-1",
                cost=0.20,
                timestamp=base + timedelta(hours=2),
            )
        )

        records = await cost_tracker.get_project_records(
            "proj-1",
            start=base + timedelta(hours=1),
        )
        assert len(records) == 1
        assert records[0].cost == pytest.approx(0.20)


class _PinningRepo:
    """Minimal in-memory repo that pins currency per project.

    Mirrors the contract Postgres / SQLite enforce: first increment
    pins the currency; subsequent increments in a different currency
    raise ``MixedCurrencyAggregationError``.
    """

    def __init__(self) -> None:
        self.pinned: dict[str, str] = {}
        self._totals: dict[str, tuple[float, int, int, int]] = {}
        self._seen: set[str] = set()

    async def get(self, project_id: str) -> ProjectCostAggregate | None:
        if project_id not in self.pinned:
            return None
        cost, in_t, out_t, count = self._totals[project_id]
        return ProjectCostAggregate(
            project_id=project_id,
            total_cost=cost,
            currency=self.pinned[project_id],
            total_input_tokens=in_t,
            total_output_tokens=out_t,
            record_count=count,
            last_updated=datetime.now(UTC),
        )

    async def increment(
        self,
        project_id: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: str,
    ) -> ProjectCostAggregate:
        from synthorg.budget.errors import (
            MixedCurrencyAggregationError,
        )

        pinned = self.pinned.get(project_id)
        if pinned is None:
            self.pinned[project_id] = currency
        elif pinned != currency:
            msg = f"project {project_id!r} pinned {pinned!r}; got {currency!r}"
            raise MixedCurrencyAggregationError(
                msg,
                currencies=frozenset({pinned, currency}),
                project_id=project_id,
            )
        prev_cost, prev_in, prev_out, prev_count = self._totals.get(
            project_id, (0.0, 0, 0, 0)
        )
        new_totals = (
            prev_cost + cost,
            prev_in + input_tokens,
            prev_out + output_tokens,
            prev_count + 1,
        )
        self._totals[project_id] = new_totals
        return ProjectCostAggregate(
            project_id=project_id,
            total_cost=new_totals[0],
            currency=self.pinned[project_id],
            total_input_tokens=new_totals[1],
            total_output_tokens=new_totals[2],
            record_count=new_totals[3],
            last_updated=datetime.now(UTC),
        )

    async def increment_if_unseen(  # noqa: PLR0913 -- mirrors the real signature
        self,
        project_id: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: str,
        claim_id: str,
        now: datetime,
        ttl_seconds: float,
    ) -> tuple[ProjectCostAggregate | None, bool]:
        _ = (now, ttl_seconds)
        if claim_id in self._seen:
            return None, False
        self._seen.add(claim_id)
        aggregate = await self.increment(
            project_id, cost, input_tokens, output_tokens, currency=currency
        )
        return aggregate, True


@pytest.mark.unit
class TestPerProjectCurrencyGuardWithoutBudgetConfig:
    """Without a budget_config, project_cost_repo-backed trackers must
    still refuse to collapse mixed-currency rows into one project
    aggregate.  Enforcement now lives in the repository (Postgres /
    SQLite); the tracker propagates the error.
    """

    async def test_first_record_pins_project_currency(self) -> None:
        """A subsequent USD write after an initial USD record is accepted."""
        tracker = CostTracker(project_cost_repo=_PinningRepo())
        await tracker.record(_make_project_record(project_id="proj-x", currency="USD"))
        await tracker.record(
            _make_project_record(
                project_id="proj-x",
                currency="USD",
                cost=0.01,
                task_id="task-2",
            )
        )

    async def test_second_record_in_different_currency_raises(self) -> None:
        """Switching currency mid-stream within the same project raises."""
        from synthorg.budget.errors import (
            MixedCurrencyAggregationError,
        )

        tracker = CostTracker(project_cost_repo=_PinningRepo())
        await tracker.record(_make_project_record(project_id="proj-y", currency="USD"))
        with pytest.raises(MixedCurrencyAggregationError) as exc_info:
            await tracker.record(
                _make_project_record(
                    project_id="proj-y",
                    currency="EUR",
                    cost=0.01,
                    task_id="task-2",
                )
            )
        assert exc_info.value.currencies == frozenset({"USD", "EUR"})

    async def test_different_projects_may_use_different_currencies(self) -> None:
        """The per-project pin is scoped to project_id, not global."""
        tracker = CostTracker(project_cost_repo=_PinningRepo())
        await tracker.record(_make_project_record(project_id="proj-a", currency="USD"))
        await tracker.record(_make_project_record(project_id="proj-b", currency="EUR"))
        # Both projects accepted their own currency; no raise.
