"""Unit tests for the ``UngroundedClaim`` model."""

import pytest
from pydantic import ValidationError

from synthorg.security.redteam.grounding.models import UngroundedClaim


@pytest.mark.unit
class TestUngroundedClaim:
    """Model is frozen, ``extra='forbid'``, confidence bounded ``[0, 1]``."""

    def test_creation_minimal(self) -> None:
        claim = UngroundedClaim(
            excerpt="Revenue grew 47% last quarter.",
            reason="numeric assertion without citation",
            confidence=0.7,
            source="heuristic",
        )
        assert claim.expected_source_kind is None

    def test_frozen(self) -> None:
        claim = UngroundedClaim(
            excerpt="x",
            reason="r",
            confidence=0.5,
            source="heuristic",
        )
        with pytest.raises(ValidationError):
            claim.confidence = 0.9  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            UngroundedClaim(
                excerpt="x",
                reason="r",
                confidence=0.5,
                source="heuristic",
                extra_unknown="boom",  # type: ignore[call-arg]
            )

    def test_confidence_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UngroundedClaim(
                excerpt="x",
                reason="r",
                confidence=-0.1,
                source="heuristic",
            )

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UngroundedClaim(
                excerpt="x",
                reason="r",
                confidence=1.1,
                source="heuristic",
            )

    def test_source_literal_enforced(self) -> None:
        with pytest.raises(ValidationError):
            UngroundedClaim(
                excerpt="x",
                reason="r",
                confidence=0.5,
                source="bogus",  # type: ignore[arg-type]
            )

    def test_substrate_source_accepts_expected_source_kind(self) -> None:
        claim = UngroundedClaim(
            excerpt="Revenue grew 47%.",
            reason="no source resolution",
            confidence=0.95,
            source="knowledge_substrate",
            expected_source_kind="finance_report",
        )
        assert claim.expected_source_kind == "finance_report"

    def test_heuristic_source_confidence_above_ceiling_rejected(self) -> None:
        """Heuristic-source claims cannot exceed the ceiling (0.7)."""
        from synthorg.security.redteam.grounding.models import (
            HEURISTIC_CONFIDENCE_CEILING,
        )

        with pytest.raises(ValidationError, match="confidence"):
            UngroundedClaim(
                excerpt="x",
                reason="r",
                confidence=HEURISTIC_CONFIDENCE_CEILING + 0.1,
                source="heuristic",
            )

    def test_heuristic_source_confidence_below_floor_rejected(self) -> None:
        """Heuristic-source claims cannot drop below the floor (0.4)."""
        from synthorg.security.redteam.grounding.models import (
            HEURISTIC_CONFIDENCE_FLOOR,
        )

        with pytest.raises(ValidationError, match="confidence"):
            UngroundedClaim(
                excerpt="x",
                reason="r",
                confidence=HEURISTIC_CONFIDENCE_FLOOR - 0.1,
                source="heuristic",
            )

    def test_substrate_source_allows_full_confidence_range(self) -> None:
        """Substrate-source claims may use the full ``[0.0, 1.0]`` range."""
        UngroundedClaim(
            excerpt="x",
            reason="r",
            confidence=0.99,
            source="knowledge_substrate",
            expected_source_kind="finance_report",
        )
        UngroundedClaim(
            excerpt="y",
            reason="r",
            confidence=0.05,
            source="knowledge_substrate",
            expected_source_kind="finance_report",
        )
