"""Tests for the shared cost-forecast row <-> model marshalling helpers."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from synthorg.budget.forecast_models import Forecast, ForecastDecision, HaltContext
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence._shared.cost_forecast_marshalling import (
    COST_FORECAST_COLUMNS,
    build_cost_forecast_where,
    forecast_save_params,
    row_to_forecast,
    validate_cost_forecast_update_keys,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _pending_forecast() -> Forecast:
    return Forecast(
        forecast_id=uuid4(),
        brief_hash="a" * 64,
        estimated_cost=10.0,
        lower_bound=8.0,
        upper_bound=12.0,
        currency="USD",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _approved_forecast_with_halt() -> Forecast:
    return Forecast(
        forecast_id=uuid4(),
        brief_hash="b" * 64,
        estimated_cost=10.0,
        lower_bound=8.0,
        upper_bound=12.0,
        currency="USD",
        decision=ForecastDecision.APPROVED,
        decided_at=_NOW,
        decided_by="operator-1",
        ceiling_amount=15.0,
        halt_context=HaltContext(
            accumulated_cost=16.0,
            ceiling_amount=15.0,
            currency="USD",
            halted_at=_NOW,
        ),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _sqlite_row(entity: Forecast) -> dict[str, object]:
    """Build a SQLite-shaped row (TEXT timestamps / string forecast_id)."""
    columns = [c.strip() for c in COST_FORECAST_COLUMNS.split(",")]
    return dict(zip(columns, forecast_save_params(entity), strict=True))


@pytest.mark.unit
class TestRowToForecast:
    """``row_to_forecast`` reconstructs a forecast from either backend shape."""

    def test_pending_round_trip(self) -> None:
        forecast = _pending_forecast()
        result = row_to_forecast(_sqlite_row(forecast))

        assert result == forecast

    def test_approved_with_halt_round_trip(self) -> None:
        forecast = _approved_forecast_with_halt()
        result = row_to_forecast(_sqlite_row(forecast))

        assert result == forecast
        assert result.halt_context is not None
        assert result.halt_context.accumulated_cost == pytest.approx(16.0)

    def test_postgres_native_uuid_and_datetime(self) -> None:
        forecast = _pending_forecast()
        row = _sqlite_row(forecast)
        row["forecast_id"] = forecast.forecast_id
        row["created_at"] = _NOW
        row["updated_at"] = _NOW

        result = row_to_forecast(row)

        assert result.forecast_id == forecast.forecast_id
        assert isinstance(result.forecast_id, UUID)
        assert result.created_at == _NOW

    def test_corrupt_row_raises_query_error(self) -> None:
        row = _sqlite_row(_pending_forecast())
        row["estimated_cost"] = "not-a-number"

        with pytest.raises(QueryError):
            row_to_forecast(row)


@pytest.mark.unit
class TestBuildCostForecastWhere:
    """``build_cost_forecast_where`` emits backend-specific placeholders."""

    def test_empty_filter_matches_all(self) -> None:
        where, params = build_cost_forecast_where(
            CostForecastFilterSpec(), placeholder="?"
        )

        assert where == "1=1"
        assert params == []

    def test_combined_filter_sqlite(self) -> None:
        spec = CostForecastFilterSpec(
            brief_hash="c" * 64, decision=ForecastDecision.APPROVED
        )
        where, params = build_cost_forecast_where(spec, placeholder="?")

        assert where == "brief_hash = ? AND decision = ?"
        assert params == ["c" * 64, ForecastDecision.APPROVED.value]

    def test_decision_filter_postgres(self) -> None:
        spec = CostForecastFilterSpec(decision=ForecastDecision.PENDING)
        where, params = build_cost_forecast_where(spec, placeholder="%s")

        assert where == "decision = %s"
        assert params == [ForecastDecision.PENDING.value]


@pytest.mark.unit
class TestValidateUpdateKeys:
    """``validate_cost_forecast_update_keys`` guards transition updates."""

    def test_allowed_keys_pass(self) -> None:
        validate_cost_forecast_update_keys(
            "transition_if",
            uuid4(),
            {"decided_by": "op-1", "decided_at": _NOW},
            to_state=ForecastDecision.APPROVED,
        )

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(QueryError):
            validate_cost_forecast_update_keys(
                "transition_if",
                uuid4(),
                {"bogus": 1},
                to_state=ForecastDecision.APPROVED,
            )

    def test_superseded_with_decided_by_raises(self) -> None:
        with pytest.raises(QueryError):
            validate_cost_forecast_update_keys(
                "transition_if",
                uuid4(),
                {"decided_by": "op-1"},
                to_state=ForecastDecision.SUPERSEDED,
            )
