"""Tests for execution-time decision options + the chosen-option round-trip.

Covers the two Surface-2 seams: the :class:`EvidencePackage` decision-option
invariants, and ``resolve_decision_reason`` which turns the operator's
``chosen_option_id`` into the decision the parked agent continues with.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from synthorg.api.controllers.approvals._shared import (
    record_chosen_option,
    resolve_decision_reason,
)
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ValidationError
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.plan import PlanOption
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
_ACTION = RecommendedAction(
    action_type=NotBlankStr("approve"),
    label=NotBlankStr("Approve"),
    description=NotBlankStr("Proceed"),
)


def _option(id_: str, *, recommended: bool = False) -> PlanOption:
    return PlanOption(
        id=NotBlankStr(id_),
        title=NotBlankStr(id_.title()),
        summary=NotBlankStr(f"tradeoffs for {id_}"),
        recommended=recommended,
    )


def _evidence(options: tuple[PlanOption, ...]) -> EvidencePackage:
    return EvidencePackage(
        id=NotBlankStr(str(as_uuid("ev-dec"))),
        title=NotBlankStr("Core engine architecture"),
        narrative=NotBlankStr("How should the core engine be structured?"),
        recommended_actions=(_ACTION,),
        options=options,
        source_agent_id=NotBlankStr("agent-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        created_at=_NOW,
    )


def _item(evidence: EvidencePackage | None) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid("appr-dec"),
        action_type=NotBlankStr("decision:project"),
        title=NotBlankStr("Project decision requested"),
        description=NotBlankStr("How should the core engine be structured?"),
        requested_by=NotBlankStr("agent-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=ApprovalSource.PARKED_CONTEXT,
        created_at=_NOW,
        evidence_package=evidence,
    )


class TestEvidencePackageOptions:
    def test_valid_decision_options_accepted(self) -> None:
        pkg = _evidence((_option("a", recommended=True), _option("b")))
        assert len(pkg.options) == 2

    def test_single_option_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _evidence((_option("a", recommended=True),))

    def test_zero_recommended_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _evidence((_option("a"), _option("b")))

    def test_two_recommended_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _evidence((_option("a", recommended=True), _option("b", recommended=True)))

    def test_chosen_option_id_requires_options(self) -> None:
        with pytest.raises(PydanticValidationError):
            EvidencePackage(
                id=NotBlankStr(str(as_uuid("ev-req"))),
                title=NotBlankStr("t"),
                narrative=NotBlankStr("n"),
                recommended_actions=(_ACTION,),
                chosen_option_id=NotBlankStr("a"),
                source_agent_id=NotBlankStr("agent-1"),
                risk_level=ApprovalRiskLevel.MEDIUM,
                created_at=_NOW,
            )


class TestResolveDecisionReason:
    def test_options_chosen_derives_writeup(self) -> None:
        item = _item(_evidence((_option("a", recommended=True), _option("b"))))
        reason = resolve_decision_reason(item, chosen_option_id="b", comment=None)
        assert reason == "B: tradeoffs for b"

    def test_missing_choice_raises(self) -> None:
        item = _item(_evidence((_option("a", recommended=True), _option("b"))))
        with pytest.raises(ValidationError, match="requires choosing an option"):
            resolve_decision_reason(item, chosen_option_id=None, comment="whatever")

    def test_unknown_choice_raises(self) -> None:
        item = _item(_evidence((_option("a", recommended=True), _option("b"))))
        with pytest.raises(ValidationError, match="does not name an option"):
            resolve_decision_reason(item, chosen_option_id="z", comment=None)

    def test_no_options_uses_comment(self) -> None:
        item = _item(None)
        reason = resolve_decision_reason(
            item, chosen_option_id=None, comment="a free-text answer"
        )
        assert reason == "a free-text answer"


class TestRecordChosenOption:
    def test_records_the_pick_onto_the_evidence(self) -> None:
        item = _item(_evidence((_option("a", recommended=True), _option("b"))))
        updated = record_chosen_option(item, chosen_option_id="b")
        assert updated is not None
        assert updated.chosen_option_id == "b"

    def test_none_for_a_non_decision_approval(self) -> None:
        item = _item(None)
        assert record_chosen_option(item, chosen_option_id=None) is None

    def test_none_when_no_option_chosen(self) -> None:
        item = _item(_evidence((_option("a", recommended=True), _option("b"))))
        assert record_chosen_option(item, chosen_option_id=None) is None
