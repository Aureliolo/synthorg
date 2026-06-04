"""Deterministic heuristic grounding checker.

Pure-regex implementation that flags assertive declarative sentences
which lack any obvious citation marker. Runs without an LLM, without
a knowledge store, and without provider state, so the deterministic
simulation harness can replay it.

The intent is calibration, not authority. Heuristic findings are
capped at :data:`synthorg.security.redteam.routing.HEURISTIC_GROUNDING_MAX_SEVERITY`
(LOW) by the gate, so the checker can never block on its own;
authoritative grounding decisions belong to a substrate-backed checker
behind the same :class:`GroundingChecker` protocol.
"""

import re
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.security.redteam.grounding.models import (
    HEURISTIC_CONFIDENCE_CEILING,
    UngroundedClaim,
)

_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")
"""Crude sentence boundary. ASCII-only by design (English-only docs)."""

_NUMERIC_CLAIM_RE: Final[re.Pattern[str]] = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*%|\b\d+(?:[.,]\d+)?\s*(?:million|billion|thousand|"
    r"users|customers|requests|nanoseconds|microseconds|milliseconds|"
    r"seconds|minutes|hours|days|years)\b",
    re.IGNORECASE,
)
"""Numeric assertions: percentages, large units, time spans.

Time-unit alternation runs from subsecond to year so latency claims
(``250 milliseconds``) are caught alongside calendar-scale durations.
The ordering keeps units grouped by scale for readability; Python
``re`` alternation matches the first viable alternative from the
current position, so order does not affect which unit wins.

These are the highest-value targets for heuristic grounding: a number
without a source is the canonical hallucination signature.
"""

_ASSERTIVE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:is|are|was|were|grew|increased|decreased|fell|rose|reached|"
    r"achieved|outperformed|leads|leading|exceeds|exceeded|surpasses)\b",
    re.IGNORECASE,
)
"""Declarative verbs that, paired with a numeric claim, indicate an assertion."""

_CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"\[\s*\d+\s*\]|\bhttps?://\S+|\(source:|\bsource:\s*\S+|\bcitation:\s*\S+|"
    r"\bsee\s+(?:fig|figure|table|appendix|section)",
    re.IGNORECASE,
)
"""Citation markers: footnotes, URLs, ``source:``, ``see figure``."""

_HEDGE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:might|may|could|possibly|perhaps|seems|appears|likely|"
    r"approximately|roughly|around|about)\b",
    re.IGNORECASE,
)
"""Hedged language. A hedged claim is not an unconditional assertion."""

_QUESTION_RE: Final[re.Pattern[str]] = re.compile(r"\?\s*$")
"""Trailing question mark. Questions are never grounded-claim candidates."""

_CODE_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```.*?```|`[^`]+`",
    re.DOTALL,
)
"""Markdown code fences and inline code. URLs inside code are not citations."""

_HEURISTIC_NUMERIC_CLAIM_CONFIDENCE: Final[float] = HEURISTIC_CONFIDENCE_CEILING
"""Numeric ungrounded claims are the most reliable heuristic signal."""


def _strip_code_blocks(text: str) -> str:
    """Replace fenced / inline code regions with spaces of equal length.

    URLs inside code blocks are examples, not citations. Replacing
    keeps offsets stable so any future caller using character offsets
    sees consistent indices, but blanks the content from regex view.

    Returns:
        The text with code regions replaced by equal-length runs of
        spaces.
    """
    return _CODE_FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)


def _split_sentences(text: str) -> list[str]:
    """Crude sentence splitter (ASCII English).

    Returns:
        The non-empty, stripped sentences; an empty list for blank input.
    """
    stripped = text.strip()
    if not stripped:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(stripped) if s.strip()]


class HeuristicGroundingChecker:
    """Deterministic regex-based ungrounded-claim flag.

    Implements :class:`synthorg.security.redteam.grounding.protocol.GroundingChecker`.

    Decision rules (in order):

    1. Skip questions, code-block content, and hedged sentences.
    2. Numeric assertions (``N%``, ``N million``, etc.) without a
       citation marker get a CEILING-confidence
       :class:`UngroundedClaim` with reason "numeric assertion without
       citation".
    3. Non-numeric declarative assertions return ``None`` -- pure
       declarative prose without numerics is too noisy for the
       heuristic; only the agent or a substrate-backed checker may
       flag it.

    Returns at most one claim per sentence; duplicates are deduplicated
    by excerpt.
    """

    async def check(
        self,
        *,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,  # noqa: ARG002 -- reserved for cache key
        project_id: NotBlankStr | None = None,  # noqa: ARG002 -- corpus-only
    ) -> tuple[UngroundedClaim, ...]:
        """Scan ``deliverable_content`` for assertive claims without citations.

        ``project_id`` is accepted for protocol compatibility but ignored:
        the heuristic is a pure-text regex pass with no corpus to scope.

        Returns:
            The ungrounded claims found (at most one per sentence,
            deduplicated by excerpt); an empty tuple when none are found.
        """
        cleaned = _strip_code_blocks(deliverable_content)
        sentences = _split_sentences(cleaned)
        if not sentences:
            return ()
        claims: list[UngroundedClaim] = []
        seen_excerpts: set[str] = set()
        for sentence in sentences:
            claim = self._evaluate_sentence(sentence)
            if claim is None:
                continue
            if claim.excerpt in seen_excerpts:
                continue
            seen_excerpts.add(claim.excerpt)
            claims.append(claim)
        return tuple(claims)

    def _evaluate_sentence(self, sentence: str) -> UngroundedClaim | None:
        """Return an :class:`UngroundedClaim` for ``sentence`` or ``None``.

        Decision order (guards first, then signal rules):

        1. Skip questions, hedged claims, or sentences that already
           carry a citation marker (the agent already provided the
           grounding evidence; we trust the citation).
        2. Numeric assertion (number/percentage/time-unit) is the
           canonical hallucination signature: a statistic without a
           source. Always flagged.
        3. Otherwise no flag. Pure declarative prose without numerics
           is too noisy for the heuristic; only the agent or a
           substrate-backed checker may flag it.
        """
        if _QUESTION_RE.search(sentence):
            return None
        if _HEDGE_RE.search(sentence):
            return None
        if _CITATION_MARKER_RE.search(sentence):
            return None
        has_numeric = bool(_NUMERIC_CLAIM_RE.search(sentence))
        has_assertion = bool(_ASSERTIVE_VERB_RE.search(sentence))
        if has_numeric and has_assertion:
            return UngroundedClaim(
                excerpt=sentence,
                reason="numeric assertion without citation",
                confidence=_HEURISTIC_NUMERIC_CLAIM_CONFIDENCE,
                source="heuristic",
                expected_source_kind=None,
            )
        if has_numeric:
            return UngroundedClaim(
                excerpt=sentence,
                reason="numeric claim without citation",
                confidence=_HEURISTIC_NUMERIC_CLAIM_CONFIDENCE,
                source="heuristic",
                expected_source_kind=None,
            )
        return None
