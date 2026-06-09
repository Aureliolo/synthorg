"""Deterministic heuristic credibility triage.

Scores each item from its source authority, topic alignment with the
brief, recency (academic sources), and low-quality red flags. Fully
deterministic, so it is cheap and replay-stable, and serves both as a
standalone strategy and as the prefilter in the hybrid strategy.
"""

import re
from collections.abc import Mapping
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.research.constants import (
    RESEARCH_AUTHORITY_ACADEMIC,
    RESEARCH_AUTHORITY_CODE,
    RESEARCH_AUTHORITY_KNOWLEDGE,
    RESEARCH_AUTHORITY_WEB,
    RESEARCH_HEURISTIC_ALIGNMENT_WEIGHT,
    RESEARCH_HEURISTIC_AUTHORITY_WEIGHT,
    RESEARCH_HEURISTIC_RECENCY_FULL_MONTHS,
    RESEARCH_HEURISTIC_RECENCY_HORIZON_MONTHS,
    RESEARCH_HEURISTIC_RECENCY_WEIGHT,
    RESEARCH_HEURISTIC_RED_FLAG_PENALTY,
    RESEARCH_RECENCY_NEUTRAL_CREDIT,
)
from synthorg.research.enums import ResearchSourceType
from synthorg.research.models import (
    AcademicSourceLocator,
    AuthorityLevel,
    ResearchBrief,
    RetrievedItem,
    SourceCredibility,
)

_MONTHS_PER_YEAR: Final[int] = 12

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

_AUTHORITY_BASE: Final[Mapping[ResearchSourceType, float]] = {
    ResearchSourceType.ACADEMIC: RESEARCH_AUTHORITY_ACADEMIC,
    ResearchSourceType.KNOWLEDGE: RESEARCH_AUTHORITY_KNOWLEDGE,
    ResearchSourceType.CODE: RESEARCH_AUTHORITY_CODE,
    ResearchSourceType.WEB: RESEARCH_AUTHORITY_WEB,
}

_AUTHORITY_LABEL: Final[Mapping[ResearchSourceType, AuthorityLevel]] = {
    ResearchSourceType.ACADEMIC: "peer_reviewed",
    ResearchSourceType.KNOWLEDGE: "published",
    ResearchSourceType.CODE: "community",
    ResearchSourceType.WEB: "community",
}

_RED_FLAG_MARKERS: Final[tuple[str, ...]] = (
    "buy now",
    "sponsored",
    "advertisement",
    "limited offer",
    "sign up now",
    "discount code",
    "affiliate link",
)


def _tokens(text: str) -> frozenset[str]:
    """Return the lowercased alphanumeric token set of *text*."""
    return frozenset(_TOKEN_RE.findall(text.lower()))


def _alignment(question: str, item: RetrievedItem) -> float:
    """Return the Jaccard overlap of the question and the item text."""
    q = _tokens(question)
    doc = _tokens(f"{item.title} {item.snippet}")
    if not q or not doc:
        return 0.0
    return len(q & doc) / len(q | doc)


def _red_flags(item: RetrievedItem) -> tuple[str, ...]:
    """Return any low-quality marketing markers found in the item text."""
    haystack = f"{item.title} {item.snippet}".lower()
    return tuple(marker for marker in _RED_FLAG_MARKERS if marker in haystack)


class HeuristicCredibilityTriage:
    """Scores credibility from deterministic source signals."""

    __slots__ = ("_clock",)

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else SystemClock()

    async def triage(
        self,
        items: tuple[RetrievedItem, ...],
        *,
        brief: ResearchBrief,
    ) -> tuple[tuple[SourceCredibility, ...], float]:
        """Return one heuristic verdict per item; never incurs LLM cost."""
        verdicts = tuple(self.score(item, brief=brief) for item in items)
        return verdicts, 0.0

    def score(self, item: RetrievedItem, *, brief: ResearchBrief) -> SourceCredibility:
        """Return the deterministic credibility verdict for one item."""
        authority_base = _AUTHORITY_BASE[item.source_type]
        alignment = _alignment(brief.question, item)
        recency_months = self._recency_months(item)
        recency_credit = _recency_credit(recency_months)
        flags = _red_flags(item)
        raw = (
            RESEARCH_HEURISTIC_AUTHORITY_WEIGHT * authority_base
            + RESEARCH_HEURISTIC_ALIGNMENT_WEIGHT * alignment
            + RESEARCH_HEURISTIC_RECENCY_WEIGHT * recency_credit
            - RESEARCH_HEURISTIC_RED_FLAG_PENALTY * len(flags)
        )
        score = min(1.0, max(0.0, raw))
        return SourceCredibility(
            ref_id=item.ref_id,
            score=score,
            authority=_AUTHORITY_LABEL[item.source_type],
            recency_months=recency_months,
            domain_alignment=alignment,
            red_flags=flags,
            passed=score >= brief.min_credibility,
        )

    def _recency_months(self, item: RetrievedItem) -> int | None:
        """Return the item's age in months when an academic year is known."""
        locator = item.citation.external
        if not isinstance(locator, AcademicSourceLocator) or locator.year is None:
            return None
        months = (self._clock.now().year - locator.year) * _MONTHS_PER_YEAR
        return max(0, months)


def _recency_credit(recency_months: int | None) -> float:
    """Return [0, 1] recency credit: full when recent, decaying with age."""
    if recency_months is None:
        return RESEARCH_RECENCY_NEUTRAL_CREDIT
    if recency_months <= RESEARCH_HEURISTIC_RECENCY_FULL_MONTHS:
        return 1.0
    if recency_months >= RESEARCH_HEURISTIC_RECENCY_HORIZON_MONTHS:
        return 0.0
    horizon = RESEARCH_HEURISTIC_RECENCY_HORIZON_MONTHS
    span = horizon - RESEARCH_HEURISTIC_RECENCY_FULL_MONTHS
    return max(0.0, (horizon - recency_months) / span)
