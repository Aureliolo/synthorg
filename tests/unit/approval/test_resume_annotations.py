"""How a decided approval is presented to the agent it resumes."""

from datetime import UTC, datetime

import pytest

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource
from synthorg.approval.questions import (
    CLARIFY_ACTION_TYPE,
    DECISION_ACTION_TYPE,
    DECLINED_QUESTION_NOTE,
)
from synthorg.approval.resume_annotations import (
    DEFAULT_RESUME_ANNOTATIONS,
    ResumeReasonProvenance,
    reason_provenance,
    resume_annotations,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.plan import PlanOption
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _evidence(*, chosen_option_id: str | None) -> EvidencePackage:
    """Build an options evidence package, optionally already decided."""
    now = datetime(2026, 5, 15, tzinfo=UTC)
    return EvidencePackage(
        id=NotBlankStr("ev-1"),
        title=NotBlankStr("Which database?"),
        narrative=NotBlankStr("Which database should the project use?"),
        recommended_actions=(
            RecommendedAction(
                action_type=NotBlankStr("approve"),
                label=NotBlankStr("Approve with the selected option"),
                description=NotBlankStr("Proceed with the option you pick."),
            ),
        ),
        options=(
            PlanOption(
                id=NotBlankStr("sqlite"),
                title=NotBlankStr("SQLite"),
                summary=NotBlankStr("Zero ops, single writer."),
                recommended=True,
            ),
            PlanOption(
                id=NotBlankStr("postgres"),
                title=NotBlankStr("PostgreSQL"),
                summary=NotBlankStr("Concurrent writers, one more service."),
            ),
        ),
        chosen_option_id=(
            NotBlankStr(chosen_option_id) if chosen_option_id is not None else None
        ),
        source_agent_id=NotBlankStr("agent-lead"),
        risk_level=ApprovalRiskLevel.LOW,
        created_at=now,
    )


def _item(
    *,
    action_type: str = CLARIFY_ACTION_TYPE,
    evidence: EvidencePackage | None = None,
) -> ApprovalItem:
    """Build a parked approval in whatever shape the case needs."""
    return ApprovalItem(
        id=as_uuid("approval-1"),
        action_type=action_type,
        title="Clarification requested",
        description="Which database should the project use?",
        requested_by="agent-lead",
        risk_level=ApprovalRiskLevel.LOW,
        source=ApprovalSource.PARKED_CONTEXT,
        created_at=datetime(2026, 5, 15, tzinfo=UTC),
        evidence_package=evidence,
    )


class TestReasonProvenance:
    def test_free_text_answer_is_the_operator_s_own(self) -> None:
        assert reason_provenance(_item()) is ResumeReasonProvenance.OPERATOR_TEXT

    def test_offered_but_unpicked_options_are_not_a_choice(self) -> None:
        # An undecided fork carries options with no pick, so the reason on
        # this item is still whatever free text the operator supplied.
        item = _item(evidence=_evidence(chosen_option_id=None))
        assert reason_provenance(item) is ResumeReasonProvenance.OPERATOR_TEXT

    def test_a_picked_option_makes_the_reason_agent_authored(self) -> None:
        item = _item(evidence=_evidence(chosen_option_id="sqlite"))
        assert reason_provenance(item) is ResumeReasonProvenance.AGENT_OPTION


class TestResumeAnnotations:
    def test_unreadable_approval_claims_no_provenance(self) -> None:
        assert resume_annotations(None, approved=False) is DEFAULT_RESUME_ANNOTATIONS

    def test_declined_question_carries_the_proceed_note(self) -> None:
        annotations = resume_annotations(_item(), approved=False)
        assert annotations.system_note == DECLINED_QUESTION_NOTE

    def test_answered_question_carries_no_note(self) -> None:
        assert resume_annotations(_item(), approved=True).system_note is None

    def test_rejected_non_question_carries_no_note(self) -> None:
        # A rejected action means stop, and it must keep meaning stop: the
        # note exists only because a declined QUESTION resumes anyway.
        item = _item(action_type="comms:external")
        assert resume_annotations(item, approved=False).system_note is None

    def test_declined_decision_carries_both_signals(self) -> None:
        item = _item(
            action_type=DECISION_ACTION_TYPE,
            evidence=_evidence(chosen_option_id="sqlite"),
        )
        annotations = resume_annotations(item, approved=False)
        assert annotations.system_note == DECLINED_QUESTION_NOTE
        assert annotations.reason_provenance is ResumeReasonProvenance.AGENT_OPTION
