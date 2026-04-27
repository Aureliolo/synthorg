"""Unit tests for CostTracker project aggregate write path."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.project_cost_aggregate import (
    ProjectCostAggregate,
)
from synthorg.budget.tracker import CostTracker

from .conftest import make_cost_record


def _make_mock_repo() -> AsyncMock:
    """Build a mock ProjectCostAggregateRepository."""
    repo = AsyncMock()
    repo.increment = AsyncMock(
        return_value=ProjectCostAggregate(
            project_id="proj-1",
            total_cost=1.0,
            currency=DEFAULT_CURRENCY,
            total_input_tokens=100,
            total_output_tokens=50,
            record_count=1,
            last_updated=datetime.now(UTC),
        ),
    )
    return repo


@pytest.mark.unit
class TestTrackerProjectAggregate:
    """Tests for CostTracker aggregate write path."""

    async def test_record_calls_repo_increment_for_project(self) -> None:
        repo = _make_mock_repo()
        tracker = CostTracker(project_cost_repo=repo)
        record = make_cost_record(project_id="proj-1", cost=1.0)

        await tracker.record(record)

        # Compare currency against the input record rather than a
        # hardcoded literal so the assertion follows the fixture's
        # source of truth -- changing ``make_cost_record`` default
        # currency does not silently desync this test.
        repo.increment.assert_awaited_once_with(
            "proj-1",
            1.0,
            record.input_tokens,
            record.output_tokens,
            currency=record.currency,
        )

    async def test_record_skips_repo_when_no_project_id(self) -> None:
        repo = _make_mock_repo()
        tracker = CostTracker(project_cost_repo=repo)
        record = make_cost_record(project_id=None)

        await tracker.record(record)

        repo.increment.assert_not_awaited()

    async def test_record_succeeds_when_repo_raises(self) -> None:
        repo = _make_mock_repo()
        repo.increment.side_effect = RuntimeError("DB down")
        tracker = CostTracker(project_cost_repo=repo)
        record = make_cost_record(project_id="proj-1")

        # Should not raise -- aggregate write is best-effort
        await tracker.record(record)

        # In-memory record still present
        count = await tracker.get_record_count()
        assert count == 1

    async def test_record_works_without_repo(self) -> None:
        tracker = CostTracker()
        record = make_cost_record(project_id="proj-1")

        await tracker.record(record)

        count = await tracker.get_record_count()
        assert count == 1

    async def test_in_memory_still_works_alongside_repo(self) -> None:
        repo = _make_mock_repo()
        tracker = CostTracker(project_cost_repo=repo)

        await tracker.record(make_cost_record(project_id="proj-1", cost=2.0))
        await tracker.record(make_cost_record(project_id="proj-1", cost=3.0))

        # In-memory queries still work
        cost = await tracker.get_project_cost("proj-1")
        assert cost == pytest.approx(5.0)
