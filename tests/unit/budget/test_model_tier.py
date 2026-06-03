"""Unit tests for the model-id to tier resolver and override map."""

import pytest

from synthorg.budget.model_tier import (
    ModelTierMap,
    heuristic_tier,
    resolve_tier,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class TestHeuristicTier:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("example-large-001", "large"),
            ("example-medium-001", "medium"),
            ("example-small-001", "small"),
            ("example-large-002", "large"),
            ("example-local-small-001", "local-small"),
            ("unknown", None),
            ("", None),
            ("acme-frontier-x", None),
        ],
    )
    def test_resolves_known_archetypes(
        self, model_id: str, expected: str | None
    ) -> None:
        assert heuristic_tier(model_id) == expected


class TestModelTierMap:
    def test_empty_map_is_default(self) -> None:
        assert dict(ModelTierMap().overrides) == {}

    def test_rejects_non_canonical_tier(self) -> None:
        with pytest.raises(ValueError, match="canonical tier"):
            ModelTierMap(overrides={NotBlankStr("acme-x"): "enormous"})

    def test_accepts_canonical_tiers(self) -> None:
        tier_map = ModelTierMap(
            overrides={
                NotBlankStr("acme-frontier-x"): "large",
                NotBlankStr("acme-edge-y"): "local-small",
            }
        )
        assert tier_map.overrides[NotBlankStr("acme-frontier-x")] == "large"


class TestResolveTier:
    def test_no_map_uses_heuristic(self) -> None:
        assert resolve_tier("example-medium-001") == "medium"
        assert resolve_tier("acme-frontier-x") is None

    def test_override_wins_over_heuristic(self) -> None:
        tier_map = ModelTierMap(overrides={NotBlankStr("example-large-001"): "small"})
        assert resolve_tier("example-large-001", tier_map) == "small"

    def test_falls_through_to_heuristic_when_unmapped(self) -> None:
        tier_map = ModelTierMap(overrides={NotBlankStr("acme-frontier-x"): "large"})
        assert resolve_tier("acme-frontier-x", tier_map) == "large"
        assert resolve_tier("example-small-001", tier_map) == "small"
        assert resolve_tier("totally-unknown", tier_map) is None
