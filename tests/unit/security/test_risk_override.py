"""Tests for RiskTierOverride model and SecOpsRiskClassifier."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability.events.security import SECURITY_RISK_FALLBACK
from synthorg.security.risk_map import (
    MapBackedRiskClassifier,
    default_risk_classifier,
)
from synthorg.security.rules.risk_override import (
    RiskTierOverride,
    SecOpsRiskClassifier,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


def _baseline() -> MapBackedRiskClassifier:
    """Build the rules-baseline classifier wrapped by SecOps overrides.

    Returns:
        A ``MapBackedRiskClassifier`` over the default map.
    """
    return default_risk_classifier(miss_event=SECURITY_RISK_FALLBACK)


_NOW = datetime.now(tz=UTC)
_FUTURE = _NOW + timedelta(hours=24)
_PAST = _NOW - timedelta(hours=1)


def _make_override(  # noqa: PLR0913
    *,
    action_type: str = "code:write",
    original: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    override: ApprovalRiskLevel = ApprovalRiskLevel.LOW,
    expires_at: datetime = _FUTURE,
    override_id: str = "ovr-1",
    revoked_at: datetime | None = None,
    revoked_by: str | None = None,
) -> RiskTierOverride:
    return RiskTierOverride(
        id=override_id,
        action_type=action_type,
        original_tier=original,
        override_tier=override,
        reason="test override",
        created_by="user-1",
        created_at=_NOW,
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_by=revoked_by,
    )


class TestRiskTierOverrideModel:
    """Tests for the RiskTierOverride Pydantic model."""

    def test_valid_override(self) -> None:
        ovr = _make_override()
        assert ovr.action_type == "code:write"
        assert ovr.original_tier == ApprovalRiskLevel.MEDIUM
        assert ovr.override_tier == ApprovalRiskLevel.LOW
        assert ovr.is_active(_NOW) is True

    def test_frozen(self) -> None:
        ovr = _make_override()
        with pytest.raises(ValidationError):
            ovr.reason = "changed"  # type: ignore[misc]

    def test_expires_before_created_rejected(self) -> None:
        with pytest.raises(ValidationError, match="expires_at"):
            _make_override(expires_at=_PAST)

    def test_same_tiers_rejected(self) -> None:
        with pytest.raises(ValidationError, match="override_tier"):
            _make_override(
                original=ApprovalRiskLevel.HIGH,
                override=ApprovalRiskLevel.HIGH,
            )

    def test_expiry_beyond_ceiling_rejected(self) -> None:
        with pytest.raises(ValidationError, match="within"):
            _make_override(expires_at=_NOW + timedelta(days=400))

    def test_expired_override_not_active(self) -> None:
        ovr = RiskTierOverride(
            id="ovr-exp",
            action_type="code:write",
            original_tier=ApprovalRiskLevel.MEDIUM,
            override_tier=ApprovalRiskLevel.LOW,
            reason="test",
            created_by="user-1",
            created_at=_NOW - timedelta(hours=2),
            expires_at=_NOW - timedelta(hours=1),
        )
        assert ovr.is_active(_NOW) is False

    def test_revoked_override_not_active(self) -> None:
        ovr = _make_override(
            revoked_at=_NOW,
            revoked_by="admin-1",
        )
        assert ovr.is_active(_NOW) is False


class TestSecOpsRiskClassifier:
    """Tests for SecOpsRiskClassifier."""

    def test_falls_back_to_base_when_no_overrides(self) -> None:
        base = _baseline()
        classifier = SecOpsRiskClassifier(base=base)
        # CODE_WRITE is MEDIUM in the default map
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.MEDIUM

    def test_active_override_takes_precedence(self) -> None:
        base = _baseline()
        ovr = _make_override(
            action_type="code:write",
            original=ApprovalRiskLevel.MEDIUM,
            override=ApprovalRiskLevel.LOW,
        )
        classifier = SecOpsRiskClassifier(base=base, overrides=(ovr,))
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.LOW

    def test_expired_override_ignored(self) -> None:
        base = _baseline()
        ovr = RiskTierOverride(
            id="ovr-exp",
            action_type="code:write",
            original_tier=ApprovalRiskLevel.MEDIUM,
            override_tier=ApprovalRiskLevel.LOW,
            reason="expired test",
            created_by="user-1",
            created_at=_NOW - timedelta(hours=2),
            expires_at=_NOW - timedelta(hours=1),
        )
        classifier = SecOpsRiskClassifier(base=base, overrides=(ovr,))
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.MEDIUM

    def test_revoked_override_ignored(self) -> None:
        base = _baseline()
        ovr = _make_override(
            revoked_at=_NOW,
            revoked_by="admin-1",
        )
        classifier = SecOpsRiskClassifier(base=base, overrides=(ovr,))
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.MEDIUM

    def test_add_override(self) -> None:
        base = _baseline()
        classifier = SecOpsRiskClassifier(base=base)
        ovr = _make_override()
        classifier.add_override(ovr)
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.LOW

    def test_revoke_override(self) -> None:
        base = _baseline()
        ovr = _make_override()
        classifier = SecOpsRiskClassifier(base=base, overrides=(ovr,))
        revoked = classifier.revoke_override("ovr-1")
        assert revoked is not None
        assert revoked.is_active(_NOW) is False
        # After revocation, falls back to base
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.MEDIUM

    def test_revoke_nonexistent_returns_none(self) -> None:
        base = _baseline()
        classifier = SecOpsRiskClassifier(base=base)
        result = classifier.revoke_override("nonexistent")
        assert result is None

    def test_active_overrides_returns_only_active(self) -> None:
        base = _baseline()
        active = _make_override(override_id="ovr-active")
        revoked = _make_override(
            override_id="ovr-revoked",
            revoked_at=_NOW,
            revoked_by="admin",
        )
        classifier = SecOpsRiskClassifier(
            base=base,
            overrides=(active, revoked),
        )
        result = classifier.active_overrides()
        assert len(result) == 1
        assert result[0].id == "ovr-active"

    def test_override_expiry_driven_by_injected_clock(self) -> None:
        base = _baseline()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        clock = FakeClock(start=start)
        ovr = RiskTierOverride(
            id="ovr-clock",
            action_type="code:write",
            original_tier=ApprovalRiskLevel.MEDIUM,
            override_tier=ApprovalRiskLevel.LOW,
            reason="clock-seam test",
            created_by="user-1",
            created_at=start,
            expires_at=start + timedelta(hours=1),
        )
        classifier = SecOpsRiskClassifier(
            base=base,
            overrides=(ovr,),
            clock=clock,
        )
        # Before expiry the override applies.
        assert classifier.classify("code:write") == ApprovalRiskLevel.LOW
        assert len(classifier.active_overrides()) == 1
        # Advancing past expiry drops it without touching wall-clock.
        clock.advance(timedelta(hours=2).total_seconds())
        assert classifier.classify("code:write") == ApprovalRiskLevel.MEDIUM
        assert classifier.active_overrides() == ()

    def test_multiple_overrides_last_active_wins(self) -> None:
        base = _baseline()
        ovr1 = _make_override(
            override_id="ovr-1",
            override=ApprovalRiskLevel.LOW,
        )
        ovr2 = _make_override(
            override_id="ovr-2",
            override=ApprovalRiskLevel.CRITICAL,
        )
        # Later override (ovr2) should win
        classifier = SecOpsRiskClassifier(
            base=base,
            overrides=(ovr1, ovr2),
        )
        result = classifier.classify("code:write")
        assert result == ApprovalRiskLevel.CRITICAL

    def test_add_duplicate_override_id_rejected(self) -> None:
        base = _baseline()
        classifier = SecOpsRiskClassifier(base=base)
        classifier.add_override(_make_override(override_id="ovr-dup"))
        with pytest.raises(ValueError, match="already registered"):
            classifier.add_override(_make_override(override_id="ovr-dup"))


def test_risk_tier_literal_matches_approval_risk_level() -> None:
    """The MCP ``RiskTierLiteral`` must stay in lockstep with the enum.

    The MCP layer deliberately keeps its own string literal rather than
    importing :class:`ApprovalRiskLevel`; this guards the two from drifting.
    """
    from typing import get_args

    from synthorg.meta.mcp.domains._security_args import RiskTierLiteral

    assert set(get_args(RiskTierLiteral)) == {
        level.value for level in ApprovalRiskLevel
    }
