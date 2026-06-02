# module-kind: code
"""Startup reconciliation for conversational-intake persistence.

With an in-memory ``ApprovalStore`` the approval queue starts empty every
boot, so any PENDING conversational proposal or invite row a previous
process committed to persistence can never be resumed (its approval is
gone). Those rows are unreachable: this module marks them terminal at
startup (proposal -> REJECTED, invite -> DECLINED) so they stop surfacing
as actionable in proposal/invite listings while the audit record of the
intake survives. On a persistent store the approvals survive restart, so
the rows stay resumable and are left untouched.
"""

from typing import TYPE_CHECKING

from synthorg.api.approval_store import ApprovalStore
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.persistence.conversational_factory import ConversationalRepositories

logger = get_logger(__name__)


async def reconcile_orphaned_conversational_intake(
    repositories: ConversationalRepositories,
    approval_store: ApprovalStoreProtocol,
) -> None:
    """Mark PENDING proposals/invites with no durable backing approval terminal.

    Each orphaned PENDING proposal moves PENDING -> REJECTED and each
    orphaned PENDING invite moves PENDING -> DECLINED via the repository
    CAS, preserving the row (audit trail) while removing it from the
    actionable set. A store of unknown type is treated as persistent
    (left untouched) to avoid retiring resumable work.
    """
    store_is_in_memory = (
        isinstance(approval_store, ApprovalStore)
        and not approval_store.has_persistent_repo
    )
    if not store_is_in_memory:
        return
    from synthorg.core.enums import (  # noqa: PLC0415
        ConversationalProposalStatus,
    )
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationInviteStatus,
    )
    from synthorg.persistence.conversation_invite_protocol import (  # noqa: PLC0415
        ConversationInviteFilterSpec,
    )
    from synthorg.persistence.conversational_proposal_protocol import (  # noqa: PLC0415
        ConversationalProposalFilterSpec,
    )

    pending_proposals = await repositories.proposal_repo.query(
        ConversationalProposalFilterSpec(status=ConversationalProposalStatus.PENDING)
    )
    rejected_proposals = 0
    for proposal in pending_proposals:
        if await repositories.proposal_repo.transition_if(
            proposal.id,
            ConversationalProposalStatus.PENDING,
            ConversationalProposalStatus.REJECTED,
        ):
            rejected_proposals += 1
    pending_invites = await repositories.invite_repo.query(
        ConversationInviteFilterSpec(status=ConversationInviteStatus.PENDING)
    )
    declined_invites = 0
    for invite in pending_invites:
        if await repositories.invite_repo.transition_if(
            invite.id,
            ConversationInviteStatus.PENDING,
            ConversationInviteStatus.DECLINED,
        ):
            declined_invites += 1
    if rejected_proposals or declined_invites:
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="retired orphaned conversational intake rows (in-memory store)",
            rejected_proposals=rejected_proposals,
            declined_invites=declined_invites,
        )


__all__ = ["reconcile_orphaned_conversational_intake"]
