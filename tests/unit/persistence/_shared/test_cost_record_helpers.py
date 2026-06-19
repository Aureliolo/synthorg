"""Tests for the shared cost-record aggregation helper."""

import pytest

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence._shared._cost_record_helpers import (
    resolve_currency_aggregate,
)


@pytest.mark.unit
class TestResolveCurrencyAggregate:
    """Single-snapshot aggregate row handling shared by both backends."""

    def test_single_currency_returns_total(self) -> None:
        total = resolve_currency_aggregate(
            (1, "USD", 12.5), agent_id="a-1", task_id=None
        )
        assert total == 12.5

    def test_none_row_raises_query_error(self) -> None:
        with pytest.raises(QueryError):
            resolve_currency_aggregate(None, agent_id="a-1", task_id="t-1")

    def test_mixed_currency_raises(self) -> None:
        with pytest.raises(MixedCurrencyAggregationError):
            resolve_currency_aggregate(
                (2, "USD,EUR", 12.5), agent_id="a-1", task_id="t-1"
            )
