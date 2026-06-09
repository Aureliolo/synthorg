"""Supersession rule classification for procedural memory proposals.

Compares a candidate proposal against an existing org-scope entry
to determine if the candidate supersedes, conflicts with, or
partially overlaps the existing one.
"""

import re
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.memory.procedural.models import ProceduralMemoryProposal
from synthorg.observability import get_logger
from synthorg.observability.events.skill_evolver import (
    SUPERSESSION_CONFLICT,
    SUPERSESSION_FULL,
    SUPERSESSION_PARTIAL,
)

logger = get_logger(__name__)

# Minimum word overlap ratio to consider conditions as overlapping.
_OVERLAP_THRESHOLD: float = 0.5

# Minimum word overlap ratio for conditions to be a "superset".
_SUPERSET_THRESHOLD: float = 0.8

_WORD_RE = re.compile(r"\w+")


class SupersessionVerdict(StrEnum):
    """Classification of how one proposal relates to another.

    Members:
        FULL: Candidate fully supersedes existing.
        PARTIAL: Conditions overlap but neither is a superset.
        CONFLICT: Same condition, contradictory actions.
    """

    FULL = "full"
    PARTIAL = "partial"
    CONFLICT = "conflict"


class SupersessionResult(BaseModel):
    """Result of supersession evaluation between two proposals.

    Attributes:
        verdict: Classification of the relationship.
        candidate_id: ID of the new proposal.
        existing_id: ID of the existing proposal.
        reason: Human-readable explanation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: SupersessionVerdict = Field(
        description="Supersession classification",
    )
    candidate_id: NotBlankStr = Field(
        description="ID of the candidate proposal",
    )
    existing_id: NotBlankStr = Field(
        description="ID of the existing proposal",
    )
    reason: NotBlankStr = Field(
        description="Explanation of the verdict",
    )


_MIN_TOKEN_LENGTH: int = 2


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric word tokens from text.

    Returns:
        Set of ``str``.
    """
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) >= _MIN_TOKEN_LENGTH}


def _overlap_ratio(a: set[str], b: set[str]) -> float:
    """Fraction of b's tokens present in a (0.0-1.0).

    Returns:
        Result of type ``float``.
    """
    if not b:
        return 1.0
    return len(a & b) / len(b)


def _similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets.

    Returns:
        Result of type ``float``.
    """
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


class _LogFn(Protocol):
    """Structured-logging callable (e.g. ``logger.warning`` / ``logger.info``)."""

    def __call__(self, event: str, /, **kwargs: object) -> object: ...


def _emit_result(  # noqa: PLR0913
    verdict: SupersessionVerdict,
    event: str,
    log_fn: _LogFn,
    candidate_id: NotBlankStr,
    existing_id: NotBlankStr,
    reason: str,
    **log_kwargs: str,
) -> SupersessionResult:
    """Log an event and build a ``SupersessionResult``.

    Returns:
        Result of type ``SupersessionResult``.
    """
    log_fn(
        event,
        candidate_id=candidate_id,
        existing_id=existing_id,
        **log_kwargs,
    )
    return SupersessionResult(
        verdict=verdict,
        candidate_id=candidate_id,
        existing_id=existing_id,
        reason=reason,
    )


def evaluate_supersession(
    candidate: ProceduralMemoryProposal,
    existing: ProceduralMemoryProposal,
    candidate_id: NotBlankStr,
    existing_id: NotBlankStr,
) -> SupersessionResult:
    """Classify the relationship between candidate and existing.

    Rules (evaluated in this order):
        - **CONFLICT**: high condition overlap but low action
          similarity (contradictory approaches).
        - **FULL**: candidate.condition is a superset of
          existing.condition, actions are compatible, AND
          candidate.confidence > existing.confidence.
        - **PARTIAL**: everything else (insufficient overlap,
          confidence not strictly higher, or empty conditions).

    Args:
        candidate: The new proposal.
        existing: The existing org-scope proposal.
        candidate_id: ID of the candidate.
        existing_id: ID of the existing entry.

    Returns:
        Classified ``SupersessionResult``.
    """
    cond_candidate = _tokenize(candidate.condition)
    cond_existing = _tokenize(existing.condition)

    # Short-circuit when either condition yields no tokens.
    if not cond_candidate or not cond_existing:
        return _emit_result(
            SupersessionVerdict.PARTIAL,
            SUPERSESSION_PARTIAL,
            logger.debug,
            candidate_id,
            existing_id,
            "Insufficient condition tokens for comparison",
            condition_similarity="n/a",
            action_similarity="n/a",
        )

    act_candidate = _tokenize(candidate.action)
    act_existing = _tokenize(existing.action)

    condition_coverage = _overlap_ratio(cond_candidate, cond_existing)
    condition_similarity = _similarity(cond_candidate, cond_existing)
    action_similarity = _similarity(act_candidate, act_existing)

    cs = f"{condition_similarity:.0%}"
    as_ = f"{action_similarity:.0%}"

    # CONFLICT: high condition overlap + low action similarity
    if (
        condition_similarity >= _OVERLAP_THRESHOLD
        and action_similarity < _OVERLAP_THRESHOLD
    ):
        return _emit_result(
            SupersessionVerdict.CONFLICT,
            SUPERSESSION_CONFLICT,
            logger.info,
            candidate_id,
            existing_id,
            f"Condition similarity {cs} but action similarity "
            f"only {as_} (contradictory approaches)",
            condition_similarity=cs,
            action_similarity=as_,
        )

    # FULL: condition superset + compatible actions + higher confidence
    if (
        condition_coverage >= _SUPERSET_THRESHOLD
        and action_similarity >= _OVERLAP_THRESHOLD
        and candidate.confidence > existing.confidence
    ):
        return _emit_result(
            SupersessionVerdict.FULL,
            SUPERSESSION_FULL,
            logger.info,
            candidate_id,
            existing_id,
            f"Candidate covers {condition_coverage:.0%} of existing "
            f"condition with higher confidence "
            f"({candidate.confidence:.2f} > {existing.confidence:.2f})",
            condition_coverage=f"{condition_coverage:.0%}",
        )

    # PARTIAL: everything else
    return _emit_result(
        SupersessionVerdict.PARTIAL,
        SUPERSESSION_PARTIAL,
        logger.debug,
        candidate_id,
        existing_id,
        f"Partial overlap: condition similarity {cs}, action similarity {as_}",
        condition_similarity=cs,
        action_similarity=as_,
    )
