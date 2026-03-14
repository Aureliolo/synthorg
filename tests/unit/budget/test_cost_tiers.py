"""Tests for cost tier definitions and classification."""

import pytest
from pydantic import ValidationError

from synthorg.budget.cost_tiers import (
    BUILTIN_TIERS,
    CostTierDefinition,
    CostTiersConfig,
    classify_model_tier,
    resolve_tiers,
)

pytestmark = pytest.mark.timeout(30)


# ── CostTierDefinition ─────────────────────────────────────────────


@pytest.mark.unit
class TestCostTierDefinition:
    """Tests for CostTierDefinition model."""

    def test_valid_minimal(self) -> None:
        """Minimal valid tier with just id and display_name."""
        tier = CostTierDefinition(id="low", display_name="Low")
        assert tier.id == "low"
        assert tier.display_name == "Low"
        assert tier.description == ""
        assert tier.price_range_min == 0.0
        assert tier.price_range_max is None
        assert tier.color == "#6b7280"
        assert tier.icon == "circle"
        assert tier.sort_order == 0

    def test_valid_full(self) -> None:
        """Full tier with all fields set."""
        tier = CostTierDefinition(
            id="custom",
            display_name="Custom Tier",
            description="A custom cost tier",
            price_range_min=0.01,
            price_range_max=0.05,
            color="#ff0000",
            icon="star",
            sort_order=5,
        )
        assert tier.price_range_min == 0.01
        assert tier.price_range_max == 0.05
        assert tier.color == "#ff0000"
        assert tier.icon == "star"
        assert tier.sort_order == 5

    def test_unbounded_max_allowed(self) -> None:
        """price_range_max=None means unbounded above."""
        tier = CostTierDefinition(
            id="premium",
            display_name="Premium",
            price_range_min=0.03,
            price_range_max=None,
        )
        assert tier.price_range_max is None

    def test_equal_min_max_rejected(self) -> None:
        """price_range_max == price_range_min is rejected (zero-width)."""
        with pytest.raises(ValidationError, match="zero-width"):
            CostTierDefinition(
                id="exact",
                display_name="Exact",
                price_range_min=0.01,
                price_range_max=0.01,
            )

    def test_max_less_than_min_rejected(self) -> None:
        """price_range_max < price_range_min raises ValueError."""
        with pytest.raises(ValidationError, match="price_range_max"):
            CostTierDefinition(
                id="bad",
                display_name="Bad",
                price_range_min=0.05,
                price_range_max=0.01,
            )

    def test_negative_min_rejected(self) -> None:
        """Negative price_range_min is rejected."""
        with pytest.raises(ValidationError):
            CostTierDefinition(
                id="neg",
                display_name="Neg",
                price_range_min=-0.01,
            )

    def test_negative_max_rejected(self) -> None:
        """Negative price_range_max is rejected."""
        with pytest.raises(ValidationError):
            CostTierDefinition(
                id="neg",
                display_name="Neg",
                price_range_max=-0.01,
            )

    def test_blank_id_rejected(self) -> None:
        """Empty id is rejected."""
        with pytest.raises(ValidationError):
            CostTierDefinition(id="", display_name="X")

    def test_whitespace_id_rejected(self) -> None:
        """Whitespace-only id is rejected."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            CostTierDefinition(id="   ", display_name="X")

    def test_blank_display_name_rejected(self) -> None:
        """Empty display_name is rejected."""
        with pytest.raises(ValidationError):
            CostTierDefinition(id="x", display_name="")

    def test_frozen(self) -> None:
        """Model is frozen (immutable)."""
        tier = CostTierDefinition(id="x", display_name="X")
        with pytest.raises(ValidationError):
            tier.id = "y"  # type: ignore[misc]

    def test_nan_rejected(self) -> None:
        """NaN values are rejected by allow_inf_nan=False."""
        with pytest.raises(ValidationError):
            CostTierDefinition(
                id="x",
                display_name="X",
                price_range_min=float("nan"),
            )

    def test_inf_rejected(self) -> None:
        """Inf values are rejected by allow_inf_nan=False."""
        with pytest.raises(ValidationError):
            CostTierDefinition(
                id="x",
                display_name="X",
                price_range_min=float("inf"),
            )


# ── CostTiersConfig ────────────────────────────────────────────────


@pytest.mark.unit
class TestCostTiersConfig:
    """Tests for CostTiersConfig model."""

    def test_defaults(self) -> None:
        """Default config has no user tiers and includes builtins."""
        cfg = CostTiersConfig()
        assert cfg.tiers == ()
        assert cfg.include_builtin is True

    def test_with_user_tiers(self) -> None:
        """User-defined tiers are accepted."""
        tier = CostTierDefinition(id="custom", display_name="Custom")
        cfg = CostTiersConfig(tiers=(tier,))
        assert len(cfg.tiers) == 1
        assert cfg.tiers[0].id == "custom"

    def test_duplicate_tier_ids_rejected(self) -> None:
        """Duplicate tier IDs within user tiers are rejected."""
        tier = CostTierDefinition(id="dup", display_name="Dup")
        with pytest.raises(ValidationError, match="Duplicate tier IDs"):
            CostTiersConfig(tiers=(tier, tier))

    def test_include_builtin_false(self) -> None:
        """include_builtin=False disables built-in tiers."""
        cfg = CostTiersConfig(include_builtin=False)
        assert cfg.include_builtin is False

    def test_frozen(self) -> None:
        """Model is frozen."""
        cfg = CostTiersConfig()
        with pytest.raises(ValidationError):
            cfg.include_builtin = False  # type: ignore[misc]


# ── BUILTIN_TIERS ──────────────────────────────────────────────────


@pytest.mark.unit
class TestBuiltinTiers:
    """Tests for built-in tier constants."""

    def test_four_builtin_tiers(self) -> None:
        """There are exactly 4 built-in tiers."""
        assert len(BUILTIN_TIERS) == 4

    def test_builtin_ids(self) -> None:
        """Built-in tiers have expected IDs."""
        ids = {t.id for t in BUILTIN_TIERS}
        assert ids == {"low", "medium", "high", "premium"}

    def test_builtin_sort_order(self) -> None:
        """Built-in tiers are ordered by sort_order."""
        orders = [t.sort_order for t in BUILTIN_TIERS]
        assert orders == sorted(orders)

    def test_premium_unbounded(self) -> None:
        """Premium tier has unbounded max."""
        premium = next(t for t in BUILTIN_TIERS if t.id == "premium")
        assert premium.price_range_max is None

    def test_low_starts_at_zero(self) -> None:
        """Low tier starts at 0.0."""
        low = next(t for t in BUILTIN_TIERS if t.id == "low")
        assert low.price_range_min == 0.0

    def test_no_gaps_in_ranges(self) -> None:
        """Adjacent tiers share boundary values (max of one == min of next)."""
        sorted_tiers = sorted(BUILTIN_TIERS, key=lambda t: t.sort_order)
        for i in range(len(sorted_tiers) - 1):
            current = sorted_tiers[i]
            next_tier = sorted_tiers[i + 1]
            assert current.price_range_max == next_tier.price_range_min


# ── resolve_tiers ──────────────────────────────────────────────────


@pytest.mark.unit
class TestResolveTiers:
    """Tests for resolve_tiers function."""

    def test_default_config_returns_builtins(self) -> None:
        """Default config resolves to just built-in tiers."""
        result = resolve_tiers(CostTiersConfig())
        assert len(result) == 4
        assert result[0].id == "low"
        assert result[-1].id == "premium"

    def test_include_builtin_false_returns_user_only(self) -> None:
        """include_builtin=False returns only user-defined tiers."""
        tier = CostTierDefinition(id="custom", display_name="Custom")
        cfg = CostTiersConfig(tiers=(tier,), include_builtin=False)
        result = resolve_tiers(cfg)
        assert len(result) == 1
        assert result[0].id == "custom"

    def test_user_override_replaces_builtin(self) -> None:
        """User tier with same ID as built-in replaces it."""
        override = CostTierDefinition(
            id="premium",
            display_name="Premium+",
            price_range_min=0.03,
            color="#a855f7",
            sort_order=3,
        )
        cfg = CostTiersConfig(tiers=(override,))
        result = resolve_tiers(cfg)
        premium = next(t for t in result if t.id == "premium")
        assert premium.display_name == "Premium+"
        assert premium.color == "#a855f7"
        # Still 4 total (override replaces, doesn't add)
        assert len(result) == 4

    def test_user_addition_adds_to_builtins(self) -> None:
        """User tier with unique ID adds to built-in tiers."""
        extra = CostTierDefinition(
            id="budget",
            display_name="Budget",
            sort_order=-1,
        )
        cfg = CostTiersConfig(tiers=(extra,))
        result = resolve_tiers(cfg)
        assert len(result) == 5
        # Budget should be first (sort_order=-1)
        assert result[0].id == "budget"

    def test_sorted_by_sort_order(self) -> None:
        """Result is always sorted by sort_order."""
        tiers = (
            CostTierDefinition(id="z", display_name="Z", sort_order=10),
            CostTierDefinition(id="a", display_name="A", sort_order=-5),
        )
        cfg = CostTiersConfig(tiers=tiers, include_builtin=False)
        result = resolve_tiers(cfg)
        assert result[0].id == "a"
        assert result[1].id == "z"

    def test_empty_config_no_builtins(self) -> None:
        """Empty tiers with no builtins returns empty tuple."""
        cfg = CostTiersConfig(include_builtin=False)
        result = resolve_tiers(cfg)
        assert result == ()


# ── classify_model_tier ────────────────────────────────────────────


@pytest.mark.unit
class TestClassifyModelTier:
    """Tests for classify_model_tier function."""

    @pytest.fixture
    def default_tiers(self) -> tuple[CostTierDefinition, ...]:
        """Resolved default tiers."""
        return resolve_tiers(CostTiersConfig())

    def test_no_matching_tier_returns_none(self) -> None:
        """Returns None when no tier matches."""
        # Tier with range [0.01, 0.02) — cost of 0.0 won't match
        tiers = (
            CostTierDefinition(
                id="narrow",
                display_name="Narrow",
                price_range_min=0.01,
                price_range_max=0.02,
            ),
        )
        assert classify_model_tier(0.0, tiers) is None

    def test_empty_tiers_returns_none(self) -> None:
        """Empty tiers always returns None."""
        assert classify_model_tier(0.01, ()) is None

    def test_negative_cost_returns_none(
        self,
        default_tiers: tuple[CostTierDefinition, ...],
    ) -> None:
        """Negative cost returns None (logged as warning)."""
        assert classify_model_tier(-0.01, default_tiers) is None

    @pytest.mark.parametrize(
        ("cost", "expected"),
        [
            (0.0, "low"),
            (0.001, "low"),
            (0.0019, "low"),
            (0.002, "medium"),
            (0.005, "medium"),
            (0.009, "medium"),
            (0.01, "high"),
            (0.02, "high"),
            (0.029, "high"),
            (0.03, "premium"),
            (0.1, "premium"),
            (1.0, "premium"),
        ],
        ids=[
            "0.000_low",
            "0.001_low",
            "0.0019_low",
            "0.002_medium_boundary",
            "0.005_medium_mid",
            "0.009_medium_near_top",
            "0.01_high_boundary",
            "0.02_high_mid",
            "0.029_high_near_top",
            "0.03_premium_boundary",
            "0.1_premium_mid",
            "1.0_premium_very_high",
        ],
    )
    def test_classification_boundaries(
        self,
        cost: float,
        expected: str,
        default_tiers: tuple[CostTierDefinition, ...],
    ) -> None:
        """Parametrized boundary tests."""
        assert classify_model_tier(cost, default_tiers) == expected
