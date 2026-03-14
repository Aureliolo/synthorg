"""Tests for personality compatibility scoring."""

import pytest

from synthorg.core.agent import PersonalityConfig
from synthorg.core.enums import (
    CollaborationPreference,
    ConflictApproach,
)
from synthorg.core.personality import (
    compute_compatibility,
    compute_team_compatibility,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestComputeCompatibility:
    """Tests for pairwise compatibility scoring."""

    def test_identical_profiles_score_high(self) -> None:
        """Identical profiles should score close to 1.0."""
        p = PersonalityConfig(
            openness=0.7,
            conscientiousness=0.8,
            extraversion=0.5,
            agreeableness=0.6,
            stress_response=0.7,
            collaboration=CollaborationPreference.TEAM,
            conflict_approach=ConflictApproach.COLLABORATE,
        )
        score = compute_compatibility(p, p)
        # Not exactly 1.0 because extraversion rewards moderate difference.
        assert score >= 0.85

    def test_opposing_profiles_score_low(self) -> None:
        """Profiles with maximally different values should score low."""
        a = PersonalityConfig(
            openness=0.0,
            conscientiousness=0.0,
            extraversion=0.0,
            agreeableness=0.0,
            stress_response=0.0,
            collaboration=CollaborationPreference.INDEPENDENT,
            conflict_approach=ConflictApproach.AVOID,
        )
        b = PersonalityConfig(
            openness=1.0,
            conscientiousness=1.0,
            extraversion=1.0,
            agreeableness=1.0,
            stress_response=1.0,
            collaboration=CollaborationPreference.TEAM,
            conflict_approach=ConflictApproach.COMPETE,
        )
        score = compute_compatibility(a, b)
        assert score < 0.5

    def test_symmetric(self) -> None:
        """score(a, b) == score(b, a)."""
        a = PersonalityConfig(openness=0.2, conscientiousness=0.9)
        b = PersonalityConfig(openness=0.8, conscientiousness=0.3)
        assert compute_compatibility(a, b) == compute_compatibility(b, a)

    def test_score_in_valid_range(self) -> None:
        """Score is always between 0.0 and 1.0."""
        a = PersonalityConfig(openness=0.1, agreeableness=0.9)
        b = PersonalityConfig(openness=0.9, agreeableness=0.1)
        score = compute_compatibility(a, b)
        assert 0.0 <= score <= 1.0

    def test_constructive_conflict_scores_high(self) -> None:
        """Two constructive conflict approaches score higher."""
        a = PersonalityConfig(conflict_approach=ConflictApproach.COLLABORATE)
        b = PersonalityConfig(conflict_approach=ConflictApproach.COMPROMISE)
        score_constructive = compute_compatibility(a, b)

        c = PersonalityConfig(conflict_approach=ConflictApproach.COMPETE)
        d = PersonalityConfig(conflict_approach=ConflictApproach.COMPETE)
        score_destructive = compute_compatibility(c, d)

        assert score_constructive > score_destructive

    def test_same_collaboration_scores_higher(self) -> None:
        """Same collaboration preference scores higher than opposite."""
        base = PersonalityConfig(collaboration=CollaborationPreference.TEAM)
        same = PersonalityConfig(collaboration=CollaborationPreference.TEAM)
        opposite = PersonalityConfig(
            collaboration=CollaborationPreference.INDEPENDENT,
        )
        assert compute_compatibility(base, same) > compute_compatibility(base, opposite)

    def test_default_profiles_compatible(self) -> None:
        """Two default profiles should be highly compatible."""
        a = PersonalityConfig()
        b = PersonalityConfig()
        score = compute_compatibility(a, b)
        assert score >= 0.8


@pytest.mark.unit
class TestComputeTeamCompatibility:
    """Tests for team-level compatibility scoring."""

    def test_single_member_returns_one(self) -> None:
        """Single-member team returns 1.0."""
        p = PersonalityConfig()
        assert compute_team_compatibility((p,)) == 1.0

    def test_empty_team_returns_one(self) -> None:
        """Empty team returns 1.0."""
        assert compute_team_compatibility(()) == 1.0

    def test_two_identical_members(self) -> None:
        """Two identical members score high."""
        p = PersonalityConfig(openness=0.7, conscientiousness=0.8)
        score = compute_team_compatibility((p, p))
        assert score >= 0.8

    def test_team_score_in_range(self) -> None:
        """Team score is always in [0.0, 1.0]."""
        members = (
            PersonalityConfig(openness=0.1),
            PersonalityConfig(openness=0.9),
            PersonalityConfig(openness=0.5),
        )
        score = compute_team_compatibility(members)
        assert 0.0 <= score <= 1.0

    def test_three_member_team_uses_all_pairs(self) -> None:
        """Team of 3 computes 3 pairwise scores."""
        a = PersonalityConfig(openness=0.1)
        b = PersonalityConfig(openness=0.5)
        c = PersonalityConfig(openness=0.9)

        team_score = compute_team_compatibility((a, b, c))

        # Manual: average of (a,b), (a,c), (b,c)
        ab = compute_compatibility(a, b)
        ac = compute_compatibility(a, c)
        bc = compute_compatibility(b, c)
        expected = (ab + ac + bc) / 3

        assert abs(team_score - expected) < 1e-10
