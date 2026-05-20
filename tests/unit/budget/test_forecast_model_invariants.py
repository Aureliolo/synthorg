"""Cross-field invariant validators on the cost-dial domain models.

These guard the construction-time invariants added alongside the DB
CHECK constraints: a forecast estimate must lie within its band, a halt
only exists once the ceiling is crossed, and a benchmark score must lie
within its confidence interval.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.budget.forecast_models import (
    Forecast,
    ForecastDecision,
    HaltContext,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _forecast(**overrides: object) -> Forecast:
    base: dict[str, object] = {
        "forecast_id": "00000000-0000-0000-0000-000000000001",
        "brief_hash": "a" * 64,
        "estimated_cost": 1.0,
        "lower_bound": 0.8,
        "upper_bound": 1.2,
        "currency": "USD",
        "decision": ForecastDecision.PENDING,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return Forecast(**base)  # type: ignore[arg-type]


def test_forecast_rejects_estimate_below_lower_bound() -> None:
    with pytest.raises(ValidationError, match="estimated_cost"):
        _forecast(estimated_cost=0.5, lower_bound=0.8, upper_bound=1.2)


def test_forecast_rejects_estimate_above_upper_bound() -> None:
    with pytest.raises(ValidationError, match="estimated_cost"):
        _forecast(estimated_cost=1.5, lower_bound=0.8, upper_bound=1.2)


def test_forecast_accepts_estimate_within_band() -> None:
    forecast = _forecast(estimated_cost=1.0, lower_bound=0.8, upper_bound=1.2)
    assert forecast.estimated_cost == 1.0


def test_halt_context_rejects_accumulated_below_ceiling() -> None:
    with pytest.raises(ValidationError, match="accumulated_cost"):
        HaltContext(
            accumulated_cost=0.9,
            ceiling_amount=1.0,
            currency="USD",
            halted_at=_NOW,
        )


def test_halt_context_accepts_accumulated_at_ceiling() -> None:
    halt = HaltContext(
        accumulated_cost=1.0,
        ceiling_amount=1.0,
        currency="USD",
        halted_at=_NOW,
    )
    assert halt.accumulated_cost == 1.0


def test_benchmark_score_rejects_score_outside_confidence_band() -> None:
    with pytest.raises(ValidationError, match="confidence band"):
        BenchmarkScore(
            score=80.0,
            confidence_lower=85.0,
            confidence_upper=95.0,
            source="stub:test",
            last_updated=_NOW,
        )


def test_benchmark_score_accepts_score_within_band() -> None:
    score = BenchmarkScore(
        score=90.0,
        confidence_lower=85.0,
        confidence_upper=95.0,
        source="stub:test",
        last_updated=_NOW,
    )
    assert score.score == 90.0
