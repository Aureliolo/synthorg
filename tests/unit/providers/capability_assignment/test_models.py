"""Unit tests for tier-assignment model invariants."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityOverride,
    CapabilityOverrideMap,
)

pytestmark = pytest.mark.unit


def _override(model_id: str) -> CapabilityOverride:
    return CapabilityOverride(
        provider="p",
        model_id=model_id,
        tier="large",
        provenance="operator",
        reason="manual",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_heuristic_assignment_allows_fractional_confidence() -> None:
    assignment = CapabilityAssignment(
        provider="p",
        model_id="m",
        tier="medium",
        provenance="heuristic",
        confidence=0.4,
        reason="cost proxy",
    )
    assert assignment.confidence == 0.4


@pytest.mark.parametrize("provenance", ["operator", "llm"])
def test_override_confidence_must_be_authoritative(provenance: str) -> None:
    with pytest.raises(ValidationError, match="authoritative"):
        CapabilityAssignment(
            provider="p",
            model_id="m",
            tier="large",
            provenance=provenance,  # type: ignore[arg-type]
            confidence=0.5,
            reason="manual",
        )


def test_map_rejects_duplicate_override_for_same_model() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        CapabilityOverrideMap(overrides=(_override("dup"), _override("dup")))


def test_map_allows_distinct_overrides() -> None:
    envelope = CapabilityOverrideMap(overrides=(_override("a"), _override("b")))
    assert len(envelope.overrides) == 2
