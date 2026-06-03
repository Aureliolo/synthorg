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

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.conversational_factory import ConversationalRepositories

logger = get_logger(__name__)


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


async def _retire_pending_items[StatusT, SpecT, ItemT: _HasId](
    repo_query: Callable[[SpecT], Awaitable[Sequence[ItemT]]],
    transition_if: Callable[[str, StatusT, StatusT], Awaitable[bool]],
    spec: SpecT,
    pending: StatusT,
    terminal: StatusT,
) -> tuple[int, int]:
    """Move every PENDING row matching *spec* to its terminal status via CAS.

    Returns:
        ``(queried, transitioned)`` -- a gap (queried greater than
        transitioned) means a concurrent process already retired some
        rows and the CAS lost the race, which is a normal non-error
        outcome that would otherwise be invisible.
    """
    items = await repo_query(spec)
    transitioned = 0
    for item in items:
        if await transition_if(item.id, pending, terminal):
            transitioned += 1
    return len(items), transitioned


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
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="conversational intake reconcile skipped: store treated as persistent",
            approval_store_type=type(approval_store).__name__,
        )
        return
    from synthorg.core.enums import ConversationalProposalStatus  # noqa: PLC0415
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationInviteStatus,
    )
    from synthorg.persistence.conversation_invite_protocol import (  # noqa: PLC0415
        ConversationInviteFilterSpec,
    )
    from synthorg.persistence.conversational_proposal_protocol import (  # noqa: PLC0415
        ConversationalProposalFilterSpec,
    )

    queried_proposals, rejected_proposals = await _retire_pending_items(
        repositories.proposal_repo.query,
        repositories.proposal_repo.transition_if,
        ConversationalProposalFilterSpec(status=ConversationalProposalStatus.PENDING),
        ConversationalProposalStatus.PENDING,
        ConversationalProposalStatus.REJECTED,
    )
    queried_invites, declined_invites = await _retire_pending_items(
        repositories.invite_repo.query,
        repositories.invite_repo.transition_if,
        ConversationInviteFilterSpec(status=ConversationInviteStatus.PENDING),
        ConversationInviteStatus.PENDING,
        ConversationInviteStatus.DECLINED,
    )
    if queried_proposals or queried_invites:
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="retired orphaned conversational intake rows (in-memory store)",
            pending_proposals=queried_proposals,
            rejected_proposals=rejected_proposals,
            pending_invites=queried_invites,
            declined_invites=declined_invites,
        )


__all__ = ["reconcile_orphaned_conversational_intake"]
