"""Unit tests for the tier-assignment API DTO mappers."""

import pytest

from synthorg.api.dto_capability_assignment import (
    to_capability_assignment_dto,
    to_capability_recommendation_dto,
)
from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityRecommendation,
)

pytestmark = pytest.mark.unit


def _assignment(provenance: str, confidence: float) -> CapabilityAssignment:
    return CapabilityAssignment(
        provider="p",
        model_id="m",
        capability="capable",
        provenance=provenance,  # type: ignore[arg-type]
        confidence=confidence,
        reason="because",
    )


def test_heuristic_assignment_is_not_an_override() -> None:
    # A heuristic tier carries the classifier's sub-1.0 confidence.
    dto = to_capability_assignment_dto(_assignment("heuristic", 0.7))
    assert dto.is_override is False
    assert dto.capability == "capable"


@pytest.mark.parametrize("provenance", ["operator", "llm"])
def test_override_provenance_flags_is_override(provenance: str) -> None:
    # An override is authoritative (confidence 1.0), enforced by the model.
    dto = to_capability_assignment_dto(_assignment(provenance, 1.0))
    assert dto.is_override is True
    assert dto.provenance == provenance


def test_recommendation_dto_round_trips_fields() -> None:
    rec = CapabilityRecommendation(
        provider="p",
        model_id="m",
        capability="expert",
        confidence=0.9,
        rationale="frontier model",
    )
    dto = to_capability_recommendation_dto(rec)
    assert dto.capability == "expert"
    assert dto.rationale == "frontier model"
