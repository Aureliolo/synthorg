"""Unit tests for source-aware grounding-claim to finding conversion."""

import pytest

from synthorg.security.redteam._grounding_findings import claim_to_finding
from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.models import RedTeamAttackSurface, RedTeamSeverity

pytestmark = pytest.mark.unit


def _substrate_claim(
    confidence: float,
    *,
    expected_source_kind: str | None = None,
) -> UngroundedClaim:
    return UngroundedClaim(
        excerpt="Revenue grew 47% last quarter.",
        reason="not supported by the project knowledge corpus",
        confidence=confidence,
        source="knowledge_substrate",
        expected_source_kind=expected_source_kind,
    )


class TestClaimToFinding:
    def test_heuristic_claim_maps_to_low(self) -> None:
        claim = UngroundedClaim(
            excerpt="Revenue grew 47% last quarter.",
            reason="numeric assertion without citation",
            confidence=0.7,
            source="heuristic",
        )
        finding = claim_to_finding(claim)
        assert finding is not None
        assert finding.attack_surface is RedTeamAttackSurface.GROUNDING
        assert finding.severity is RedTeamSeverity.LOW
        assert finding.source == "heuristic"

    @pytest.mark.parametrize(
        ("confidence", "expected"),
        [
            (0.95, RedTeamSeverity.HIGH),
            (0.7, RedTeamSeverity.MEDIUM),
            (0.5, RedTeamSeverity.LOW),
        ],
    )
    def test_substrate_claim_escalates_by_confidence(
        self,
        confidence: float,
        expected: RedTeamSeverity,
    ) -> None:
        finding = claim_to_finding(_substrate_claim(confidence))
        assert finding is not None
        assert finding.severity is expected
        assert finding.source == "knowledge_substrate"

    def test_high_substrate_finding_carries_evidence(self) -> None:
        # HIGH findings require at least one evidence entry; the excerpt
        # supplies it, so construction must not raise.
        finding = claim_to_finding(_substrate_claim(0.95))
        assert finding is not None
        assert finding.severity is RedTeamSeverity.HIGH
        assert len(finding.evidence) >= 1
        assert finding.evidence[0]

    def test_substrate_claim_below_drop_floor_is_dropped(self) -> None:
        # substrate_severity_for_confidence returns None below the drop
        # floor; the conversion drops the claim entirely rather than
        # coercing it into a LOW finding, so below-floor noise never
        # reaches the verdict (the checker never emits such a claim, but
        # the conversion enforces the contract regardless).
        assert claim_to_finding(_substrate_claim(0.2)) is None

    def test_expected_source_kind_appears_in_suggested_fix(self) -> None:
        finding = claim_to_finding(
            _substrate_claim(0.9, expected_source_kind="finance_report")
        )
        assert finding is not None
        assert finding.suggested_fix is not None
        assert "finance_report" in finding.suggested_fix
