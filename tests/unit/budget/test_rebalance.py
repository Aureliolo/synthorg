"""Tests for budget rebalance utility."""

from typing import cast

import pytest

from synthorg.budget.rebalance import (
    RebalanceMode,
    RebalanceResult,
    compute_rebalance,
)


def _dept(name: str, budget: float) -> dict[str, object]:
    return {"name": name, "budget_percent": budget}


# ── NONE mode ────────────────────────────────────────────────


@pytest.mark.unit
class TestRebalanceModeNone:
    """NONE mode concatenates departments without adjustment."""

    def test_concatenates_without_change(self) -> None:
        existing = [_dept("eng", 60), _dept("prod", 40)]
        new = [_dept("security", 8)]
        result = compute_rebalance(existing, new, RebalanceMode.NONE)

        assert len(result.departments) == 3
        assert result.departments[0]["budget_percent"] == 60
        assert result.departments[1]["budget_percent"] == 40
        assert result.departments[2]["budget_percent"] == 8
        assert result.new_total == 108
        assert result.scale_factor is None
        assert result.rejected is False

    def test_empty_new_depts(self) -> None:
        existing = [_dept("eng", 60)]
        result = compute_rebalance(existing, [], RebalanceMode.NONE)

        assert len(result.departments) == 1
        assert result.new_total == 60
        assert result.rejected is False

    def test_empty_existing_depts(self) -> None:
        result = compute_rebalance([], [_dept("sec", 20)], RebalanceMode.NONE)

        assert len(result.departments) == 1
        assert result.old_total == 0
        assert result.new_total == 20
        assert result.rejected is False

    def test_both_empty(self) -> None:
        result = compute_rebalance([], [], RebalanceMode.NONE)

        assert len(result.departments) == 0
        assert result.new_total == 0


# ── SCALE_EXISTING mode ─────────────────────────────────────


@pytest.mark.unit
class TestRebalanceModeScaleExisting:
    """SCALE_EXISTING scales down existing departments proportionally."""

    def test_scales_proportionally(self) -> None:
        existing = [_dept("eng", 60), _dept("prod", 40)]
        new = [_dept("security", 8)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.rejected is False
        assert result.scale_factor is not None
        assert result.scale_factor < 1.0
        # 92 / 100 = 0.92
        assert result.scale_factor == pytest.approx(0.92, abs=1e-6)
        # eng: 60 * 0.92 = 55.2, prod: 40 * 0.92 = 36.8
        assert result.departments[0]["budget_percent"] == pytest.approx(
            55.2,
            abs=1e-6,
        )
        assert result.departments[1]["budget_percent"] == pytest.approx(
            36.8,
            abs=1e-6,
        )
        assert result.departments[2]["budget_percent"] == 8
        assert result.new_total == pytest.approx(100.0, abs=1e-6)

    def test_no_scaling_when_under_budget(self) -> None:
        existing = [_dept("eng", 50), _dept("prod", 30)]
        new = [_dept("security", 10)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.scale_factor == 1.0
        assert result.departments[0]["budget_percent"] == 50
        assert result.departments[1]["budget_percent"] == 30
        assert result.new_total == 90
        assert result.rejected is False

    def test_no_scaling_when_exactly_100(self) -> None:
        existing = [_dept("eng", 60), _dept("prod", 30)]
        new = [_dept("security", 10)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.scale_factor == 1.0
        assert result.new_total == 100.0

    def test_zero_existing_budgets_under_max(self) -> None:
        """Zero-budget existing depts + new under 100% -- no scaling needed."""
        existing = [_dept("eng", 0), _dept("prod", 0)]
        new = [_dept("security", 20)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.scale_factor == 1.0
        assert result.departments[0]["budget_percent"] == 0
        assert result.departments[1]["budget_percent"] == 0
        assert result.new_total == 20
        assert result.rejected is False

    def test_zero_existing_budgets_over_max(self) -> None:
        """Zero-budget existing depts + new over 100% -- factor clamped to 0."""
        existing = [_dept("eng", 0), _dept("prod", 0)]
        new = [_dept("security", 110)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.scale_factor == 0.0
        assert result.departments[0]["budget_percent"] == 0
        assert result.departments[1]["budget_percent"] == 0
        assert result.new_total == 110
        assert result.rejected is False

    def test_new_depts_total_exceeds_max(self) -> None:
        """When new depts alone exceed 100%, existing all go to 0."""
        existing = [_dept("eng", 60)]
        new = [_dept("sec", 50), _dept("data", 60)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.scale_factor == 0.0
        assert result.departments[0]["budget_percent"] == 0

    def test_empty_new_depts(self) -> None:
        existing = [_dept("eng", 60)]
        result = compute_rebalance(
            existing,
            [],
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.scale_factor == 1.0
        assert result.new_total == 60
        assert len(result.departments) == 1

    def test_empty_existing_depts(self) -> None:
        result = compute_rebalance(
            [],
            [_dept("sec", 20)],
            RebalanceMode.SCALE_EXISTING,
        )

        assert result.new_total == 20
        assert result.scale_factor == 1.0
        assert len(result.departments) == 1

    def test_rounding_precision_honored(self) -> None:
        """Ensure no IEEE 754 artifacts leak through."""
        existing = [_dept("eng", 33.33), _dept("prod", 33.33), _dept("ops", 33.34)]
        new = [_dept("sec", 10)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
        )

        total = sum(cast("float", d["budget_percent"]) for d in result.departments)
        assert round(total, 10) == pytest.approx(100.0, abs=1e-8)

    def test_does_not_mutate_input_dicts(self) -> None:
        existing = [_dept("eng", 60), _dept("prod", 40)]
        new = [_dept("sec", 20)]
        compute_rebalance(existing, new, RebalanceMode.SCALE_EXISTING)

        assert existing[0]["budget_percent"] == 60
        assert existing[1]["budget_percent"] == 40


# ── REJECT_IF_OVER mode ─────────────────────────────────────


@pytest.mark.unit
class TestRebalanceModeRejectIfOver:
    """REJECT_IF_OVER rejects when total exceeds max_budget."""

    def test_rejects_when_over(self) -> None:
        existing = [_dept("eng", 60), _dept("prod", 40)]
        new = [_dept("sec", 8)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.REJECT_IF_OVER,
        )

        assert result.rejected is True
        assert result.new_total == 108
        assert result.scale_factor is None

    def test_accepts_when_under(self) -> None:
        existing = [_dept("eng", 50)]
        new = [_dept("sec", 10)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.REJECT_IF_OVER,
        )

        assert result.rejected is False
        assert result.new_total == 60

    def test_accepts_when_exactly_100(self) -> None:
        existing = [_dept("eng", 60)]
        new = [_dept("sec", 40)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.REJECT_IF_OVER,
        )

        assert result.rejected is False
        assert result.new_total == 100.0

    def test_departments_unchanged_on_rejection(self) -> None:
        existing = [_dept("eng", 60), _dept("prod", 40)]
        new = [_dept("sec", 8)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.REJECT_IF_OVER,
        )

        assert result.departments[0]["budget_percent"] == 60
        assert result.departments[1]["budget_percent"] == 40
        assert result.departments[2]["budget_percent"] == 8


# ── Parametrized edge cases ──────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("existing", "new", "mode", "expected_total", "expected_rejected"),
    [
        pytest.param([], [], RebalanceMode.NONE, 0, False, id="both-empty-none"),
        pytest.param(
            [], [], RebalanceMode.SCALE_EXISTING, 0, False, id="both-empty-scale"
        ),
        pytest.param(
            [], [], RebalanceMode.REJECT_IF_OVER, 0, False, id="both-empty-reject"
        ),
        pytest.param(
            [_dept("a", 100)],
            [_dept("b", 0)],
            RebalanceMode.SCALE_EXISTING,
            100,
            False,
            id="new-zero-budget-no-scale",
        ),
        pytest.param(
            [_dept("a", 50)],
            [_dept("b", 0)],
            RebalanceMode.REJECT_IF_OVER,
            50,
            False,
            id="new-zero-budget-not-rejected",
        ),
    ],
)
def test_edge_cases(
    existing: list[dict[str, object]],
    new: list[dict[str, object]],
    mode: RebalanceMode,
    expected_total: float,
    expected_rejected: bool,
) -> None:
    result = compute_rebalance(existing, new, mode)
    assert result.new_total == pytest.approx(expected_total, abs=1e-8)
    assert result.rejected is expected_rejected


# ── Custom max_budget ────────────────────────────────────────


@pytest.mark.unit
class TestCustomMaxBudget:
    """Tests with non-default max_budget values."""

    def test_scale_with_custom_max(self) -> None:
        existing = [_dept("eng", 40), _dept("prod", 40)]
        new = [_dept("sec", 10)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.SCALE_EXISTING,
            max_budget=50.0,
        )

        assert result.scale_factor is not None
        assert result.scale_factor == pytest.approx(0.5, abs=1e-6)
        assert result.new_total == pytest.approx(50.0, abs=1e-6)

    def test_reject_with_custom_max(self) -> None:
        existing = [_dept("eng", 40)]
        new = [_dept("sec", 20)]
        result = compute_rebalance(
            existing,
            new,
            RebalanceMode.REJECT_IF_OVER,
            max_budget=50.0,
        )

        assert result.rejected is True
        assert result.new_total == 60


# ── RebalanceResult is a NamedTuple ──────────────────────────


@pytest.mark.unit
def test_result_is_named_tuple() -> None:
    result = compute_rebalance([], [], RebalanceMode.NONE)
    assert isinstance(result, RebalanceResult)
    assert isinstance(result, tuple)
    departments, old_total, new_total, scale_factor, rejected = result
    assert departments == ()
    assert old_total == 0
    assert new_total == 0
    assert scale_factor is None
    assert rejected is False
