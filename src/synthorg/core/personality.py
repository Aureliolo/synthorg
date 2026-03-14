"""Personality compatibility scoring.

Computes pairwise and team-level compatibility scores from
:class:`~synthorg.core.agent.PersonalityConfig` profiles.
"""

import itertools
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.enums import CollaborationPreference, ConflictApproach
from synthorg.observability import get_logger
from synthorg.observability.events.personality import (
    PERSONALITY_COMPATIBILITY_COMPUTED,
    PERSONALITY_TEAM_SCORE_COMPUTED,
)

if TYPE_CHECKING:
    from synthorg.core.agent import PersonalityConfig

logger = get_logger(__name__)

# ── Weight configuration ─────────────────────────────────────────

_WEIGHT_BIG_FIVE = 0.6
_WEIGHT_COLLABORATION = 0.2
_WEIGHT_CONFLICT = 0.2

# Big Five dimension weights (sum to 1.0 within the Big Five component).
_BF_OPENNESS = 0.2
_BF_CONSCIENTIOUSNESS = 0.25
_BF_EXTRAVERSION = 0.15
_BF_AGREEABLENESS = 0.25
_BF_STRESS = 0.15

# Collaboration adjacency: INDEPENDENT <-> PAIR <-> TEAM
_COLLAB_ORDER: MappingProxyType[CollaborationPreference, int] = MappingProxyType(
    {
        CollaborationPreference.INDEPENDENT: 0,
        CollaborationPreference.PAIR: 1,
        CollaborationPreference.TEAM: 2,
    }
)

# Conflict approach pair scoring.
# Constructive combos score high; destructive combos score low.
_CONSTRUCTIVE = frozenset({ConflictApproach.COLLABORATE, ConflictApproach.COMPROMISE})
_DESTRUCTIVE_PAIRS = frozenset(
    {
        (ConflictApproach.COMPETE, ConflictApproach.COMPETE),
        (ConflictApproach.AVOID, ConflictApproach.AVOID),
    }
)


def compute_compatibility(a: PersonalityConfig, b: PersonalityConfig) -> float:
    """Compute pairwise compatibility score between two personality profiles.

    Args:
        a: First personality profile.
        b: Second personality profile.

    Returns:
        Score between 0.0 (incompatible) and 1.0 (highly compatible).
    """
    bf_score = _big_five_score(a, b)
    collab_score = _collaboration_score(a.collaboration, b.collaboration)
    conflict_score = _conflict_score(a.conflict_approach, b.conflict_approach)

    result = (
        _WEIGHT_BIG_FIVE * bf_score
        + _WEIGHT_COLLABORATION * collab_score
        + _WEIGHT_CONFLICT * conflict_score
    )
    # Clamp to [0.0, 1.0] for safety.
    result = max(0.0, min(1.0, result))

    logger.debug(
        PERSONALITY_COMPATIBILITY_COMPUTED,
        score=result,
        big_five=bf_score,
        collaboration=collab_score,
        conflict=conflict_score,
    )
    return result


def compute_team_compatibility(
    members: tuple[PersonalityConfig, ...],
) -> float:
    """Compute average pairwise compatibility for a team.

    Args:
        members: Tuple of personality profiles for team members.

    Returns:
        Average pairwise score (1.0 for teams with fewer than 2 members).
    """
    team_size = len(members)
    if team_size <= 1:
        logger.debug(
            PERSONALITY_TEAM_SCORE_COMPUTED,
            team_size=team_size,
            score=1.0,
        )
        return 1.0

    pair_count = team_size * (team_size - 1) // 2
    total = sum(
        compute_compatibility(a, b) for a, b in itertools.combinations(members, 2)
    )
    result = total / pair_count

    logger.debug(
        PERSONALITY_TEAM_SCORE_COMPUTED,
        team_size=team_size,
        pair_count=pair_count,
        score=result,
    )
    return result


# ── Private scoring helpers ──────────────────────────────────────


def _big_five_score(a: PersonalityConfig, b: PersonalityConfig) -> float:
    """Weighted Big Five similarity score.

    Openness, conscientiousness, agreeableness, stress_response benefit
    from similarity. Extraversion benefits from moderate difference
    (optimal difference: 0.3, scored via tent function).
    """
    # Similarity: 1.0 - |diff|
    openness_sim = 1.0 - abs(a.openness - b.openness)
    consc_sim = 1.0 - abs(a.conscientiousness - b.conscientiousness)
    agree_sim = 1.0 - abs(a.agreeableness - b.agreeableness)
    stress_sim = 1.0 - abs(a.stress_response - b.stress_response)

    # Extraversion: moderate difference is ideal (complement).
    # Peak at 0.3 difference, using a tent-function scoring.
    extra_diff = abs(a.extraversion - b.extraversion)
    optimal_diff = 0.3
    extra_score = 1.0 - abs(extra_diff - optimal_diff) / max(
        optimal_diff, 1.0 - optimal_diff
    )

    return (
        _BF_OPENNESS * openness_sim
        + _BF_CONSCIENTIOUSNESS * consc_sim
        + _BF_EXTRAVERSION * extra_score
        + _BF_AGREEABLENESS * agree_sim
        + _BF_STRESS * stress_sim
    )


def _collaboration_score(
    a: CollaborationPreference,
    b: CollaborationPreference,
) -> float:
    """Score collaboration alignment."""
    diff = abs(_COLLAB_ORDER[a] - _COLLAB_ORDER[b])
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.5
    return 0.2


def _conflict_score(a: ConflictApproach, b: ConflictApproach) -> float:
    """Score conflict approach complementarity."""
    if a in _CONSTRUCTIVE and b in _CONSTRUCTIVE:
        return 1.0
    if (a, b) in _DESTRUCTIVE_PAIRS or (b, a) in _DESTRUCTIVE_PAIRS:
        return 0.2
    # Mixed: one constructive + one non-constructive, or moderate combos.
    if a in _CONSTRUCTIVE or b in _CONSTRUCTIVE:
        return 0.6
    return 0.4
