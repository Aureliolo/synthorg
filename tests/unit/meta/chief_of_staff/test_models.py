"""Unit tests for Chief of Staff domain models."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from synthorg.meta.chief_of_staff.models import (
    Alert,
    ChatQuery,
    ChatResponse,
    OrgInflection,
    OutcomeStats,
    ProposalOutcome,
)
from synthorg.meta.models import ProposalAltitude, RuleSeverity

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)


# ── ProposalOutcome ───────────────────────────────────────────────


class TestProposalOutcome:
    """ProposalOutcome model tests."""

    def _make(self, **overrides: object) -> ProposalOutcome:
        defaults: dict[str, object] = {
            "proposal_id": uuid4(),
            "title": "Lower quality threshold",
            "altitude": ProposalAltitude.CONFIG_TUNING,
            "decision": "approved",
            "confidence_at_decision": 0.75,
            "decided_at": _NOW,
            "decided_by": "human-reviewer",
        }
        defaults.update(overrides)
        return ProposalOutcome(**defaults)  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        outcome = self._make()
        with pytest.raises(ValidationError):
            outcome.decision = "rejected"  # type: ignore[misc]

    def test_approved_decision(self) -> None:
        outcome = self._make(decision="approved")
        assert outcome.decision == "approved"

    def test_rejected_decision(self) -> None:
        outcome = self._make(decision="rejected")
        assert outcome.decision == "rejected"

    def test_invalid_decision(self) -> None:
        with pytest.raises(ValidationError):
            self._make(decision="maybe")

    def test_confidence_bounds_low(self) -> None:
        with pytest.raises(ValidationError):
            self._make(confidence_at_decision=-0.1)

    def test_confidence_bounds_high(self) -> None:
        with pytest.raises(ValidationError):
            self._make(confidence_at_decision=1.1)

    def test_confidence_boundary_zero(self) -> None:
        outcome = self._make(confidence_at_decision=0.0)
        assert outcome.confidence_at_decision == 0.0

    def test_confidence_boundary_one(self) -> None:
        outcome = self._make(confidence_at_decision=1.0)
        assert outcome.confidence_at_decision == 1.0

    def test_source_rule_optional(self) -> None:
        outcome = self._make(source_rule=None)
        assert outcome.source_rule is None

    def test_source_rule_set(self) -> None:
        outcome = self._make(source_rule="quality_declining")
        assert outcome.source_rule == "quality_declining"

    def test_decision_reason_optional(self) -> None:
        outcome = self._make(decision_reason=None)
        assert outcome.decision_reason is None

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(title="   ")

    def test_rejects_nan_confidence(self) -> None:
        with pytest.raises(ValidationError):
            self._make(confidence_at_decision=float("nan"))


# ── OutcomeStats ──────────────────────────────────────────────────


class TestOutcomeStats:
    """OutcomeStats model tests."""

    def _make(self, **overrides: object) -> OutcomeStats:
        defaults: dict[str, object] = {
            "rule_name": "quality_declining",
            "altitude": ProposalAltitude.CONFIG_TUNING,
            "total_proposals": 10,
            "approved_count": 8,
            "rejected_count": 2,
            "last_updated": _NOW,
        }
        defaults.update(overrides)
        return OutcomeStats(**defaults)  # type: ignore[arg-type]

    def test_approval_rate_computed(self) -> None:
        stats = self._make(total_proposals=10, approved_count=8)
        assert stats.approval_rate == pytest.approx(0.8)

    def test_approval_rate_zero(self) -> None:
        stats = self._make(
            total_proposals=5,
            approved_count=0,
            rejected_count=5,
        )
        assert stats.approval_rate == pytest.approx(0.0)

    def test_approval_rate_all_approved(self) -> None:
        stats = self._make(
            total_proposals=5,
            approved_count=5,
            rejected_count=0,
        )
        assert stats.approval_rate == pytest.approx(1.0)

    def test_frozen(self) -> None:
        stats = self._make()
        with pytest.raises(ValidationError):
            stats.total_proposals = 20  # type: ignore[misc]

    def test_total_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._make(total_proposals=0)

    def test_counts_must_sum_to_total(self) -> None:
        with pytest.raises(ValidationError, match="approved_count"):
            self._make(
                total_proposals=10,
                approved_count=3,
                rejected_count=4,
            )

    def test_counts_sum_correctly(self) -> None:
        stats = self._make(
            total_proposals=7,
            approved_count=3,
            rejected_count=4,
        )
        assert stats.total_proposals == 7


# ── OrgInflection ─────────────────────────────────────────────────


class TestOrgInflection:
    """OrgInflection model tests."""

    def _make(self, **overrides: object) -> OrgInflection:
        defaults: dict[str, object] = {
            "severity": RuleSeverity.WARNING,
            "affected_domains": ("performance",),
            "metric_name": "quality_score",
            "old_value": 0.80,
            "new_value": 0.60,
            "description": "Quality score dropped 25%",
            "detected_at": _NOW,
        }
        defaults.update(overrides)
        return OrgInflection(**defaults)  # type: ignore[arg-type]

    def test_change_ratio_computed(self) -> None:
        inf = self._make(old_value=0.80, new_value=0.60)
        assert inf.change_ratio == pytest.approx(0.25)

    def test_change_ratio_zero_when_same(self) -> None:
        inf = self._make(old_value=0.50, new_value=0.50)
        assert inf.change_ratio == pytest.approx(0.0)

    def test_change_ratio_old_zero_new_nonzero(self) -> None:
        inf = self._make(old_value=0.0, new_value=0.5)
        # Symmetric formula: |0.5-0| / max(|0|, |0.5|) = 1.0
        assert inf.change_ratio == pytest.approx(1.0)

    def test_change_ratio_both_zero(self) -> None:
        inf = self._make(old_value=0.0, new_value=0.0)
        assert inf.change_ratio == pytest.approx(0.0)

    def test_auto_id(self) -> None:
        inf = self._make()
        assert isinstance(inf.id, UUID)

    def test_frozen(self) -> None:
        inf = self._make()
        with pytest.raises(ValidationError):
            inf.severity = RuleSeverity.CRITICAL  # type: ignore[misc]


# ── Alert ─────────────────────────────────────────────────────────


class TestAlert:
    """Alert model tests."""

    def _make(self, **overrides: object) -> Alert:
        defaults: dict[str, object] = {
            "severity": RuleSeverity.WARNING,
            "alert_type": "inflection",
            "description": "Budget overspend detected",
            "affected_domains": ("budget",),
            "emitted_at": _NOW,
        }
        defaults.update(overrides)
        return Alert.model_validate(defaults)

    def test_auto_id(self) -> None:
        alert = self._make()
        assert isinstance(alert.id, UUID)

    def test_frozen(self) -> None:
        alert = self._make()
        with pytest.raises(ValidationError):
            alert.severity = RuleSeverity.CRITICAL  # type: ignore[misc]

    def test_invalid_alert_type(self) -> None:
        with pytest.raises(ValidationError):
            self._make(alert_type="unknown")

    def test_signal_context_deep_copied(self) -> None:
        ctx = {"key": [1, 2, 3]}
        alert = self._make(signal_context=ctx)
        ctx["key"].append(4)
        assert alert.signal_context["key"] == [1, 2, 3]

    def test_recommended_action_optional(self) -> None:
        alert = self._make(recommended_action=None)
        assert alert.recommended_action is None


# ── ChatQuery / ChatResponse ─────────────────────────────────────


class TestChatQuery:
    """ChatQuery model tests."""

    def test_question_only(self) -> None:
        q = ChatQuery(question="Why is quality declining?")
        assert q.proposal_id is None
        assert q.alert_id is None

    def test_with_proposal_id(self) -> None:
        pid = uuid4()
        q = ChatQuery(question="Explain this", proposal_id=pid)
        assert q.proposal_id == pid

    def test_blank_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatQuery(question="   ")


class TestChatResponse:
    """ChatResponse model tests."""

    def test_defaults(self) -> None:
        r = ChatResponse(answer="Quality is declining because...")
        assert r.sources == ()
        assert r.confidence == pytest.approx(0.5)

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ChatResponse(answer="answer", confidence=1.5)

    def test_frozen(self) -> None:
        r = ChatResponse(answer="answer")
        with pytest.raises(ValidationError):
            r.answer = "new"  # type: ignore[misc]
