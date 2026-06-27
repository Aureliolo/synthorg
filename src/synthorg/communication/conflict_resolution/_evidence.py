"""Deterministic evidence extraction + scoring for positions.

Splits a position's free-text ``reasoning`` into individual claims and
assigns each a strength weight from surface features (quantitative tokens
and causal connectives). Shared by :class:`EvidenceWeightedResolver` to
pick a winner and by every strategy's ``build_dissent_records`` to attach
``minority_evidence`` to the overruled positions.

The heuristic is intentionally deterministic (no LLM): the same reasoning
always yields the same evidence and score, so the synthesis is auditable.
"""

import re
from typing import Final

from synthorg.communication.conflict_resolution.models import (
    ConflictPosition,
    EvidenceItem,
)
from synthorg.core.types import NotBlankStr

# Surface markers that raise a claim's weight: a digit anywhere signals a
# quantitative claim; a causal connective signals a supported inference.
_BASE_WEIGHT: Final[float] = 0.3
_QUANTITATIVE_BONUS: Final[float] = 0.4
_CAUSAL_BONUS: Final[float] = 0.3
_CAUSAL_MARKERS: Final[tuple[str, ...]] = (
    "because",
    "since",
    "therefore",
    "thus",
    "due to",
    "leads to",
    "results in",
    "as a result",
    "consequently",
    "hence",
)
_CLAIM_SPLIT: Final[re.Pattern[str]] = re.compile(r"[.;\n]+")
_DIGIT: Final[re.Pattern[str]] = re.compile(r"\d")


def _claim_weight(claim: str) -> float:
    """Score a single claim in ``[0, 1]`` from its surface features.

    Returns:
        ``_BASE_WEIGHT`` plus bonuses for quantitative and causal content,
        clamped to 1.0.
    """
    weight = _BASE_WEIGHT
    if _DIGIT.search(claim):
        weight += _QUANTITATIVE_BONUS
    lowered = claim.casefold()
    if any(marker in lowered for marker in _CAUSAL_MARKERS):
        weight += _CAUSAL_BONUS
    return min(weight, 1.0)


def extract_evidence(reasoning: str) -> tuple[EvidenceItem, ...]:
    """Split ``reasoning`` into weighted evidence items.

    Args:
        reasoning: The free-text justification of a position.

    Returns:
        One :class:`EvidenceItem` per non-empty claim; empty when the
        reasoning yields no claims.
    """
    items: list[EvidenceItem] = []
    for fragment in _CLAIM_SPLIT.split(reasoning):
        claim = fragment.strip()
        if not claim:
            continue
        items.append(
            EvidenceItem(
                claim=NotBlankStr(claim),
                weight=round(_claim_weight(claim), 4),
            )
        )
    return tuple(items)


def score_position(position: ConflictPosition) -> float:
    """Total the evidence weight backing a position.

    Args:
        position: The position whose reasoning to score.

    Returns:
        The summed weight of every extracted evidence item (0.0 when the
        reasoning carries no claims).
    """
    return round(sum(item.weight for item in extract_evidence(position.reasoning)), 4)
