"""Tests for trust domain enumerations."""

import pytest

from synthorg.security.trust.enums import TrustChangeReason, TrustStrategyType

pytestmark = pytest.mark.timeout(30)


# ── TrustStrategyType ────────────────────────────────────────────


@pytest.mark.unit
class TestTrustStrategyType:
    """Tests for TrustStrategyType enum values."""

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (TrustStrategyType.DISABLED, "disabled"),
            (TrustStrategyType.WEIGHTED, "weighted"),
            (TrustStrategyType.PER_CATEGORY, "per_category"),
            (TrustStrategyType.MILESTONE, "milestone"),
        ],
    )
    def test_member_value(
        self,
        member: TrustStrategyType,
        value: str,
    ) -> None:
        assert member.value == value

    def test_members_are_strings(self) -> None:
        for member in TrustStrategyType:
            assert isinstance(member, str)

    def test_member_count(self) -> None:
        assert len(TrustStrategyType) == 4


# ── TrustChangeReason ────────────────────────────────────────────


@pytest.mark.unit
class TestTrustChangeReason:
    """Tests for TrustChangeReason enum values."""

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (TrustChangeReason.SCORE_THRESHOLD, "score_threshold"),
            (TrustChangeReason.MILESTONE_ACHIEVED, "milestone_achieved"),
            (TrustChangeReason.HUMAN_APPROVAL, "human_approval"),
            (TrustChangeReason.TRUST_DECAY, "trust_decay"),
            (TrustChangeReason.RE_VERIFICATION_FAILED, "re_verification_failed"),
            (TrustChangeReason.MANUAL, "manual"),
            (TrustChangeReason.ERROR_RATE, "error_rate"),
        ],
    )
    def test_member_value(
        self,
        member: TrustChangeReason,
        value: str,
    ) -> None:
        assert member.value == value

    def test_members_are_strings(self) -> None:
        for member in TrustChangeReason:
            assert isinstance(member, str)

    def test_member_count(self) -> None:
        assert len(TrustChangeReason) == 7
