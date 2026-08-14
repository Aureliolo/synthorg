"""Unit tests for the model-id to capability resolver and override map."""

import pytest
from pydantic import ValidationError

from synthorg.budget.model_capability import (
    ModelCapabilityMap,
    heuristic_capability,
    heuristic_is_local,
    resolve_capability,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class TestHeuristicCapability:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("example-expert-001", "expert"),
            ("example-capable-001", "capable"),
            ("example-basic-001", "basic"),
            ("example-expert-002", "expert"),
            # Locality does not change the rung it reports.
            ("example-local-basic-001", "basic"),
            ("unknown", None),
            ("", None),
            ("acme-frontier-x", None),
            # Matching is anchored: a rung word that is not the leading
            # ``example-<rung>`` segment is not silently classified.
            ("foo-local-bar-basic-baz", None),
            ("acme-expert-x", None),
            ("company-expert-v1", None),
        ],
    )
    def test_resolves_known_archetypes(
        self, model_id: str, expected: str | None
    ) -> None:
        assert heuristic_capability(model_id) == expected

    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("example-local-basic-001", True),
            ("example-basic-001", False),
            ("example-expert-001", False),
            # ``local`` elsewhere in the id says nothing about hosting.
            ("acme-local-thing", False),
            ("", False),
        ],
    )
    def test_locality_is_a_separate_question(
        self, model_id: str, expected: bool
    ) -> None:
        assert heuristic_is_local(model_id) is expected


class TestModelCapabilityMap:
    def test_empty_map_is_default(self) -> None:
        assert dict(ModelCapabilityMap().overrides) == {}

    def test_rejects_non_canonical_rung(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilityMap(overrides={NotBlankStr("acme-x"): "enormous"})  # type: ignore[dict-item]

    def test_rejects_the_retired_local_small_rung(self) -> None:
        """``local-small`` conflated two axes and is no longer a rung."""
        with pytest.raises(ValidationError):
            ModelCapabilityMap(overrides={NotBlankStr("acme-y"): "local-small"})  # type: ignore[dict-item]

    def test_accepts_canonical_rungs(self) -> None:
        capability_map = ModelCapabilityMap(
            overrides={
                NotBlankStr("acme-frontier-x"): "expert",
                NotBlankStr("acme-edge-y"): "basic",
            }
        )
        assert capability_map.overrides[NotBlankStr("acme-frontier-x")] == "expert"


class TestResolveCapability:
    def test_no_map_uses_heuristic(self) -> None:
        assert resolve_capability("example-capable-001") == "capable"
        assert resolve_capability("acme-frontier-x") is None

    def test_override_wins_over_heuristic(self) -> None:
        capability_map = ModelCapabilityMap(
            overrides={NotBlankStr("example-expert-001"): "basic"},
        )
        assert resolve_capability("example-expert-001", capability_map) == "basic"

    def test_falls_through_to_heuristic_when_unmapped(self) -> None:
        capability_map = ModelCapabilityMap(
            overrides={NotBlankStr("acme-frontier-x"): "expert"},
        )
        assert resolve_capability("acme-frontier-x", capability_map) == "expert"
        assert resolve_capability("example-basic-001", capability_map) == "basic"
        assert resolve_capability("totally-unknown", capability_map) is None
