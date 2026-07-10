"""Unit tests for the tier-assignment API DTO mappers."""

import pytest

from synthorg.api.dto_tier_assignment import (
    to_tier_assignment_dto,
    to_tier_recommendation_dto,
)
from synthorg.providers.tier_assignment.models import (
    TierAssignment,
    TierRecommendation,
)

pytestmark = pytest.mark.unit


def _assignment(provenance: str) -> TierAssignment:
    return TierAssignment(
        provider="p",
        model_id="m",
        tier="medium",
        provenance=provenance,  # type: ignore[arg-type]
        confidence=0.7,
        reason="because",
    )


def test_heuristic_assignment_is_not_an_override() -> None:
    dto = to_tier_assignment_dto(_assignment("heuristic"))
    assert dto.is_override is False
    assert dto.tier == "medium"


@pytest.mark.parametrize("provenance", ["operator", "llm"])
def test_override_provenance_flags_is_override(provenance: str) -> None:
    dto = to_tier_assignment_dto(_assignment(provenance))
    assert dto.is_override is True
    assert dto.provenance == provenance


def test_recommendation_dto_round_trips_fields() -> None:
    rec = TierRecommendation(
        provider="p",
        model_id="m",
        tier="large",
        confidence=0.9,
        rationale="frontier model",
    )
    dto = to_tier_recommendation_dto(rec)
    assert dto.tier == "large"
    assert dto.rationale == "frontier model"
