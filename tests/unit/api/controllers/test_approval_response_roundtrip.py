"""An approval response must be rebuildable from its own serialised form.

Every decision runs under the idempotency guard: the callback returns
``response.model_dump(mode="json")``, the service caches it durably, and
``_decide_idempotent`` re-validates it so a repeated key replays a typed
response. That round trip is not incidental, it is the path EVERY approve and
reject takes.

It was broken for any approval carrying an evidence package. A dump includes
the ``is_fully_signed`` computed field (clients read it, and the replayed body
has to match the first one), and ``extra="forbid"`` then refused the very dict
the model had produced. The write had already committed by then, so the
operator was told an irreversible action failed when it had not, on a modal
that says "This action cannot be undone".

Asserted against the invariant rather than against that one field: a computed
field added anywhere in the response tree breaks the same way, and would
otherwise ship the same silent 500.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.controllers.approvals._shared import (
    ApprovalResponse,
    UrgencyLevel,
    to_response_without_context,
)
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.evidence import (
    EvidencePackage,
    EvidencePackageSignature,
    RecommendedAction,
)
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _evidence() -> EvidencePackage:
    return EvidencePackage(
        id=NotBlankStr(sid("evidence-1")),
        title=NotBlankStr("Hire a Completion Reviewer"),
        narrative=NotBlankStr("Nobody holds the role, so no work can be reviewed."),
        recommended_actions=(
            RecommendedAction(
                action_type=NotBlankStr("approve"),
                label=NotBlankStr("Approve the hire"),
                description=NotBlankStr("Register the candidate on the roster."),
            ),
        ),
        source_agent_id=NotBlankStr("review-staffing-reconciler"),
        risk_level=ApprovalRiskLevel.HIGH,
        created_at=_NOW,
    )


def _item(evidence: EvidencePackage | None) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid("appr-1"),
        action_type=NotBlankStr("org:hire"),
        title=NotBlankStr("Approve to continue"),
        description="A decision the operator has to take",
        requested_by=NotBlankStr("review-staffing-reconciler"),
        risk_level=ApprovalRiskLevel.HIGH,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        evidence_package=evidence,
    )


class TestTheIdempotencyReplayRoundTrip:
    def test_a_response_carrying_evidence_survives_its_own_dump(self) -> None:
        response = to_response_without_context(_item(_evidence()), now=_NOW)

        replayed = ApprovalResponse.model_validate(response.model_dump(mode="json"))

        assert replayed.evidence_package is not None
        assert replayed.evidence_package.title == "Hire a Completion Reviewer"

    def test_a_response_carrying_no_evidence_survives_too(self) -> None:
        """The commoner case, and the reason this went unnoticed for so long."""
        response = to_response_without_context(_item(None), now=_NOW)

        replayed = ApprovalResponse.model_validate(response.model_dump(mode="json"))

        assert replayed.evidence_package is None

    def test_the_computed_signature_verdict_is_recomputed_not_lost(self) -> None:
        # The dump carries `signatures` and `signature_threshold`, so the
        # computed field comes back identical rather than being dropped.
        signed = _evidence().model_copy(
            update={
                "signatures": (
                    EvidencePackageSignature(
                        approver_id=NotBlankStr("operator"),
                        signature_bytes=b"signature",
                        algorithm="ed25519",
                        signed_at=_NOW,
                        chain_position=0,
                    ),
                )
            }
        )
        response = to_response_without_context(_item(signed), now=_NOW)
        assert response.evidence_package is not None
        assert response.evidence_package.is_fully_signed is True

        replayed = ApprovalResponse.model_validate(response.model_dump(mode="json"))

        assert replayed.evidence_package is not None
        assert replayed.evidence_package.is_fully_signed is True

    def test_the_replayed_body_matches_the_first_one(self) -> None:
        """A repeat caller must not receive a different response."""
        response = to_response_without_context(_item(_evidence()), now=_NOW)
        first = response.model_dump(mode="json")

        replayed = ApprovalResponse.model_validate(first).model_dump(mode="json")

        assert replayed == first

    def test_urgency_survives_the_round_trip(self) -> None:
        response = to_response_without_context(_item(_evidence()), now=_NOW)

        replayed = ApprovalResponse.model_validate(response.model_dump(mode="json"))

        assert replayed.urgency_level is UrgencyLevel.NO_EXPIRY
