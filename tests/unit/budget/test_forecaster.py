"""Unit tests for the CostForecaster hybrid algorithm."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.forecast_models import ForecastDecision
from synthorg.budget.forecaster import (
    BriefSignal,
    CostForecaster,
    compute_brief_hash,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_FIXED_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _config(  # noqa: PLR0913 -- test helper exposes every static prior knob
    *,
    prior_large: float = 0.10,
    prior_medium: float = 0.03,
    prior_small: float = 0.005,
    prior_local_small: float = 0.0,
    prior_weight: float = 5.0,
    currency: str = "USD",
) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=100.0,
        currency=currency,
        forecast_static_prior_per_turn_large=prior_large,
        forecast_static_prior_per_turn_medium=prior_medium,
        forecast_static_prior_per_turn_small=prior_small,
        forecast_static_prior_per_turn_local_small=prior_local_small,
        forecast_shrinkage_prior_weight=prior_weight,
    )


def _signal(
    *,
    brief_text: str = "Build the marketing site",
    role_skeleton: tuple[str, ...] = ("Engineer", "Designer"),
    assignments: dict[str, str] | None = None,
    currency: str = "USD",
    turns: float | None = 8.0,
) -> BriefSignal:
    return BriefSignal(
        brief_text=brief_text,
        role_skeleton=role_skeleton,
        model_assignments=assignments if assignments is not None else {},
        currency=currency,
        estimated_turns_per_role=turns,
    )


class TestCostForecaster:
    async def test_cold_start_uses_static_prior(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        signal = _signal(
            role_skeleton=("Engineer",),
            assignments={"Engineer": "example-medium-001"},
            turns=8.0,
        )

        forecast = await forecaster.forecast(signal)

        # 8 turns * 0.03 = 0.24 (medium tier prior)
        assert forecast.estimated_cost == pytest.approx(0.24)
        assert forecast.decision is ForecastDecision.PENDING
        assert forecast.currency == "USD"
        assert forecast.created_at == _FIXED_NOW

    async def test_uncertainty_band_brackets_estimate(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        forecast = await forecaster.forecast(_signal())

        assert forecast.lower_bound <= forecast.estimated_cost <= forecast.upper_bound
        assert forecast.lower_bound >= 0.0

    async def test_cold_start_band_is_forty_percent(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        forecast = await forecaster.forecast(
            _signal(role_skeleton=("Engineer",), turns=10.0)
        )

        point = forecast.estimated_cost
        assert forecast.lower_bound == pytest.approx(point * 0.60)
        assert forecast.upper_bound == pytest.approx(point * 1.40)

    async def test_history_shrinks_toward_observed_mean(self) -> None:
        async def history(tier: str, _role_id: str) -> Sequence[float]:
            if tier == "medium":
                return (0.10, 0.10, 0.10, 0.10, 0.10)
            return ()

        forecaster = CostForecaster(
            budget_config=_config(prior_weight=5.0),
            history_lookup=history,
            clock=FakeClock(start=_FIXED_NOW).now,
        )
        forecast = await forecaster.forecast(
            _signal(
                role_skeleton=("Engineer",),
                assignments={"Engineer": "example-medium-001"},
                turns=10.0,
            )
        )

        # Prior: 0.03/turn, weight 5; observations: 0.10 mean, n=5;
        # blended_per_turn = (5*0.03 + 5*0.10) / (5+5) = 0.065
        # estimate = 0.065 * 10 = 0.65
        assert forecast.estimated_cost == pytest.approx(0.65)

    async def test_blend_bounded_by_prior_and_history(self) -> None:
        """Blend lies in [min(prior, history_mean), max(prior, history_mean)]."""

        async def history(_tier: str, _role_id: str) -> Sequence[float]:
            return (0.10, 0.10, 0.10)

        forecaster = CostForecaster(
            budget_config=_config(prior_medium=0.03, prior_weight=5.0),
            history_lookup=history,
            clock=FakeClock(start=_FIXED_NOW).now,
        )
        forecast = await forecaster.forecast(
            _signal(
                role_skeleton=("Engineer",),
                assignments={"Engineer": "example-medium-001"},
                turns=1.0,
            )
        )
        # The per-turn estimate must sit between 0.03 (prior) and 0.10
        # (history mean).
        assert 0.03 <= forecast.estimated_cost <= 0.10

    async def test_tier_lookup_falls_back_to_medium(self) -> None:
        """Unknown model id -> medium tier prior."""
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        unknown = _signal(
            role_skeleton=("Engineer",),
            assignments={"Engineer": "no-such-pattern"},
            turns=10.0,
        )
        forecast = await forecaster.forecast(unknown)
        # medium prior: 0.03 * 10 = 0.30
        assert forecast.estimated_cost == pytest.approx(0.30)

    async def test_static_prior_per_tier_respected(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        large_only = _signal(
            role_skeleton=("Engineer",),
            assignments={"Engineer": "example-large-001"},
            turns=10.0,
        )
        small_only = _signal(
            role_skeleton=("Engineer",),
            assignments={"Engineer": "example-small-001"},
            turns=10.0,
        )

        large_forecast = await forecaster.forecast(large_only)
        small_forecast = await forecaster.forecast(small_only)

        # large prior: 0.10 * 10 = 1.00; small prior: 0.005 * 10 = 0.05
        assert large_forecast.estimated_cost == pytest.approx(1.00)
        assert small_forecast.estimated_cost == pytest.approx(0.05)

    async def test_tier_resolves_through_normalised_assignment_key(self) -> None:
        """A case-divergent assignment key still resolves to its tier.

        ``compute_brief_hash`` normalises ``model_assignments`` keys, so the
        tier lookup must normalise too. A raw lookup would miss the differently
        cased key and silently fall back to the medium prior, producing a
        forecast that disagrees with the brief hash.
        """
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        signal = _signal(
            role_skeleton=("Engineer",),
            assignments={"engineer": "example-large-001"},
            turns=10.0,
        )

        forecast = await forecaster.forecast(signal)

        # Large prior (0.10 * 10 = 1.00), not the medium fallback (0.30).
        assert forecast.estimated_cost == pytest.approx(1.00)

    async def test_tier_resolves_through_stripped_model_id(self) -> None:
        """A whitespace-padded model id resolves to its tier, matching the hash.

        ``compute_brief_hash`` strips ``model_assignments`` values, so the tier
        lookup must strip too; otherwise a padded id misses ``tier_from_model_id``
        and falls back to medium, making the forecast disagree with the hash.
        """
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        signal = _signal(
            role_skeleton=("Engineer",),
            assignments={"Engineer": "  example-large-001  "},
            turns=10.0,
        )

        forecast = await forecaster.forecast(signal)

        # Large prior (0.10 * 10 = 1.00), not the medium fallback (0.30).
        assert forecast.estimated_cost == pytest.approx(1.00)

    async def test_empty_role_skeleton_raises(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        with pytest.raises(ValueError, match="role_skeleton is empty"):
            await forecaster.forecast(
                _signal(role_skeleton=()),
            )

    async def test_currency_stamp_from_signal(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        forecast = await forecaster.forecast(_signal(currency="GBP"))
        assert forecast.currency == "GBP"

    async def test_fresh_uuid_per_forecast(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        first = await forecaster.forecast(_signal())
        second = await forecaster.forecast(_signal())
        assert first.forecast_id != second.forecast_id

    async def test_brief_hash_deterministic(self) -> None:
        signal = _signal()
        assert compute_brief_hash(signal) == compute_brief_hash(signal)

    async def test_brief_hash_differs_when_text_differs(self) -> None:
        a = _signal(brief_text="Build A")
        b = _signal(brief_text="Build B")
        assert compute_brief_hash(a) != compute_brief_hash(b)

    async def test_brief_hash_differs_when_roles_differ(self) -> None:
        a = _signal(role_skeleton=("Engineer",))
        b = _signal(role_skeleton=("Designer",))
        assert compute_brief_hash(a) != compute_brief_hash(b)

    async def test_brief_hash_case_insensitive_role(self) -> None:
        """Canonicalisation lowercases role names."""
        upper = _signal(role_skeleton=("Engineer",))
        lower = _signal(role_skeleton=("engineer",))
        assert compute_brief_hash(upper) == compute_brief_hash(lower)

    async def test_brief_hash_differs_when_currency_differs(self) -> None:
        assert compute_brief_hash(_signal(currency="USD")) != compute_brief_hash(
            _signal(currency="GBP")
        )

    async def test_brief_hash_in_forecast_row(self) -> None:
        forecaster = CostForecaster(
            budget_config=_config(), clock=FakeClock(start=_FIXED_NOW).now
        )
        signal = _signal()
        forecast = await forecaster.forecast(signal)
        assert forecast.brief_hash == compute_brief_hash(signal)
