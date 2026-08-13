"""Unit tests for the model-id to tier resolver and override map."""

import pytest

from synthorg.budget.model_capability import (
    ModelCapabilityMap,
    heuristic_capability,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class TestHeuristicTier:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("example-expert-001", "large"),
            ("example-capable-001", "medium"),
            ("example-basic-001", "small"),
            ("example-expert-002", "large"),
            ("example-local-basic-001", "local-small"),
            ("local-small", "local-small"),
            ("unknown", None),
            ("", None),
            ("acme-frontier-x", None),
            # Tightened matching: a non-contiguous local+small id is not the
            # `local-small` archetype, and a tier token that is not the
            # leading `example-<tier>` segment is not silently classified.
            ("foo-local-bar-small-baz", None),
            ("acme-large-x", None),
            ("company-large-v1", None),
        ],
    )
    def test_resolves_known_archetypes(
        self, model_id: str, expected: str | None
    ) -> None:
        assert heuristic_capability(model_id) == expected


class TestModelCapabilityMap:
    def test_empty_map_is_default(self) -> None:
        assert dict(ModelCapabilityMap().overrides) == {}

    def test_rejects_non_canonical_tier(self) -> None:
        with pytest.raises(ValueError, match="canonical tier"):
            ModelCapabilityMap(overrides={NotBlankStr("acme-x"): "enormous"})

    def test_accepts_canonical_tiers(self) -> None:
        tier_map = ModelCapabilityMap(
            overrides={
                NotBlankStr("acme-frontier-x"): "large",
                NotBlankStr("acme-edge-y"): "local-small",
            }
        )
        assert tier_map.overrides[NotBlankStr("acme-frontier-x")] == "large"


class TestResolveTier:
    def test_no_map_uses_heuristic(self) -> None:
        assert resolve_capability("example-capable-001") == "medium"
        assert resolve_capability("acme-frontier-x") is None

    def test_override_wins_over_heuristic(self) -> None:
        tier_map = ModelCapabilityMap(overrides={NotBlankStr("example-expert-001"): "small"})
        assert resolve_capability("example-expert-001", tier_map) == "small"

    def test_falls_through_to_heuristic_when_unmapped(self) -> None:
        tier_map = ModelCapabilityMap(overrides={NotBlankStr("acme-frontier-x"): "large"})
        assert resolve_capability("acme-frontier-x", tier_map) == "large"
        assert resolve_capability("example-basic-001", tier_map) == "small"
        assert resolve_capability("totally-unknown", tier_map) is None
