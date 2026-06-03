"""Unit tests for the heuristic grounding stub.

The stub is the only grounding implementation today. Tests cover its
positive flags (numeric assertions without citations), its negative
flags (citation markers, hedging, questions, code blocks), and its
confidence bounds.
"""

import pytest

from synthorg.security.redteam._grounding_findings import evidence_excerpt
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.grounding.models import (
    HEURISTIC_CONFIDENCE_CEILING,
    HEURISTIC_CONFIDENCE_FLOOR,
    UngroundedClaim,
)


def _claim(excerpt: str) -> UngroundedClaim:
    return UngroundedClaim(
        excerpt=excerpt, reason="numeric claim", confidence=0.5, source="heuristic"
    )


@pytest.mark.unit
class TestEvidenceExcerpt:
    """``evidence_excerpt`` never exceeds its cap, including tiny caps."""

    def test_short_excerpt_returned_verbatim(self) -> None:
        assert evidence_excerpt(_claim("brief"), max_chars=240) == "brief"

    def test_long_excerpt_truncated_with_ellipsis(self) -> None:
        result = evidence_excerpt(_claim("x" * 50), max_chars=10)
        assert len(result) == 10
        assert result == f"{'x' * 7}..."

    @pytest.mark.parametrize(
        ("max_chars", "expected"),
        [(0, ""), (1, "."), (2, ".."), (3, "...")],
    )
    def test_tiny_cap_is_honoured(self, max_chars: int, expected: str) -> None:
        # A cap at or below the ellipsis width must never overflow to "...".
        assert evidence_excerpt(_claim("y" * 20), max_chars=max_chars) == expected


@pytest.fixture
def checker() -> HeuristicGroundingChecker:
    return HeuristicGroundingChecker()


@pytest.mark.unit
class TestPositiveFlags:
    """Sentences the heuristic SHOULD flag."""

    @pytest.mark.asyncio
    async def test_percentage_claim_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Revenue grew 47% last quarter."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert len(claims) == 1
        assert "47%" in claims[0].excerpt
        assert claims[0].source == "heuristic"

    @pytest.mark.asyncio
    async def test_large_number_claim_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "The platform reached 12 million users."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert len(claims) >= 1
        assert any("12 million" in c.excerpt for c in claims)

    @pytest.mark.asyncio
    async def test_time_unit_claim_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = (
            "Latency was 250 milliseconds on average. "
            "The system was online for 365 days without incident."
        )
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        excerpts = " | ".join(c.excerpt for c in claims)
        assert "250 milliseconds" in excerpts
        assert "365 days" in excerpts


@pytest.mark.unit
class TestNegativeFlags:
    """Sentences the heuristic should NOT flag."""

    @pytest.mark.asyncio
    async def test_cited_claim_not_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Revenue grew 47% last quarter [1]."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert claims == ()

    @pytest.mark.asyncio
    async def test_url_cited_claim_not_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = (
            "Revenue grew 47% last quarter according to https://example.com/q-report."
        )
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert claims == ()

    @pytest.mark.asyncio
    async def test_hedged_claim_not_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Revenue may have grown roughly 47% last quarter."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert claims == ()

    @pytest.mark.asyncio
    async def test_question_not_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Did revenue grow 47% last quarter?"
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert claims == ()

    @pytest.mark.asyncio
    async def test_url_inside_code_block_not_a_citation(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = (
            "Revenue grew 47% last quarter.\n"
            "Example usage: `curl https://example.com/api`."
        )
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        # The first sentence has the numeric claim and no real citation;
        # the URL in the inline code block must not count as a citation.
        assert len(claims) == 1
        assert "47%" in claims[0].excerpt

    @pytest.mark.asyncio
    async def test_pure_prose_no_numbers_not_flagged(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "The service exposes a login endpoint."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert claims == ()


@pytest.mark.unit
class TestConfidenceBounds:
    """Confidence stays within heuristic floor / ceiling."""

    @pytest.mark.asyncio
    async def test_confidence_within_bounds(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Revenue grew 47% last quarter."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert len(claims) == 1
        c = claims[0]
        assert c.confidence >= HEURISTIC_CONFIDENCE_FLOOR
        assert c.confidence <= HEURISTIC_CONFIDENCE_CEILING


@pytest.mark.unit
class TestDeduplication:
    """Repeated identical sentences produce a single claim."""

    @pytest.mark.asyncio
    async def test_repeated_sentence_deduped(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Revenue grew 47% last quarter. Revenue grew 47% last quarter."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert len(claims) == 1


@pytest.mark.unit
class TestEmptyAndWhitespace:
    """Empty / whitespace-only input returns ``()``."""

    @pytest.mark.asyncio
    async def test_whitespace_only(self, checker: HeuristicGroundingChecker) -> None:
        # Whitespace-only would fail NotBlankStr on the gate's review
        # input, but the checker itself accepts any non-blank string;
        # we still confirm graceful behaviour at the lower bound.
        text = "   \t\n"
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert claims == ()


@pytest.mark.unit
class TestProtocolCompliance:
    """Checker satisfies the GroundingChecker runtime-checkable protocol."""

    @pytest.mark.asyncio
    async def test_returns_tuple_of_ungrounded_claims(
        self, checker: HeuristicGroundingChecker
    ) -> None:
        text = "Revenue grew 47% last quarter."
        claims = await checker.check(deliverable_content=text, execution_id="e1")
        assert isinstance(claims, tuple)
        for c in claims:
            assert isinstance(c, UngroundedClaim)
