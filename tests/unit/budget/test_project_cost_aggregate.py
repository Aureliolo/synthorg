"""Unit tests for the ProjectCostAggregate model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.budget.project_cost_aggregate import ProjectCostAggregate
from synthorg.persistence.project_cost_aggregate_protocol import (
    ProjectCostAggregateRepository,
)


@pytest.mark.unit
class TestProjectCostAggregate:
    """Tests for the ProjectCostAggregate frozen model."""

    def test_valid_construction(self) -> None:
        agg = ProjectCostAggregate(
            project_id="proj-1",
            total_cost=10.5,
            currency="USD",
            total_input_tokens=1000,
            total_output_tokens=500,
            record_count=3,
            last_updated=datetime.now(UTC),
        )
        assert agg.project_id == "proj-1"
        assert agg.total_cost == 10.5
        assert agg.currency == "USD"
        assert agg.total_input_tokens == 1000
        assert agg.total_output_tokens == 500
        assert agg.record_count == 3

    def test_frozen(self) -> None:
        agg = ProjectCostAggregate(
            project_id="proj-1",
            total_cost=1.0,
            currency="USD",
            total_input_tokens=100,
            total_output_tokens=50,
            record_count=1,
            last_updated=datetime.now(UTC),
        )
        with pytest.raises(ValidationError):
            agg.total_cost = 999.0  # type: ignore[misc]

    def test_rejects_blank_project_id(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCostAggregate(
                project_id="   ",
                total_cost=0.0,
                currency="USD",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
                last_updated=datetime.now(UTC),
            )

    def test_rejects_negative_cost(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCostAggregate(
                project_id="proj-1",
                total_cost=-1.0,
                currency="USD",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
                last_updated=datetime.now(UTC),
            )

    def test_rejects_negative_tokens(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCostAggregate(
                project_id="proj-1",
                total_cost=0.0,
                currency="USD",
                total_input_tokens=-1,
                total_output_tokens=0,
                record_count=0,
                last_updated=datetime.now(UTC),
            )

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCostAggregate(
                project_id="proj-1",
                total_cost=float("nan"),
                currency="USD",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
                last_updated=datetime.now(UTC),
            )

    def test_rejects_inf(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCostAggregate(
                project_id="proj-1",
                total_cost=float("inf"),
                currency="USD",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
                last_updated=datetime.now(UTC),
            )

    def test_rejects_unknown_currency(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCostAggregate(
                project_id="proj-1",
                total_cost=1.0,
                currency="ZZZ",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
                last_updated=datetime.now(UTC),
            )

    def test_protocol_is_runtime_checkable(self) -> None:
        class _RepoStub:
            async def get(
                self,
                project_id: str,
            ) -> None:
                return None

            async def increment(
                self,
                project_id: str,
                cost: float,
                input_tokens: int,
                output_tokens: int,
                *,
                currency: str,
            ) -> None:
                _ = currency

            async def increment_if_unseen(  # noqa: PLR0913 -- mirrors protocol
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
            ) -> tuple[None, bool]:
                _ = (currency, claim_id, now, ttl_seconds)
                return None, True

        assert isinstance(_RepoStub(), ProjectCostAggregateRepository)
