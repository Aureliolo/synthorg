# module-kind: orchestrator
"""Conversational approval-resume flows for the approvals controller.

Extracted from ``_approval_review_gate.py`` (which keeps the
parked-context + review-gate flows and the ``signal_resume_intent``
dispatcher) so each resume concern stays within its size tier as the
agent-invite flow joins the conversational-intake flow.

Two flows live here, both keyed off the persisted
:attr:`ApprovalItem.source` discriminator and both repo-direct (never
through the gated feature services), so a decided approval resolves
even after its feature is toggled off:

- :func:`try_conversational_intake_resume` -- run a decided
  conversational-intake proposal (``CONVERSATIONAL_INTAKE``).
- :func:`try_conversational_invite_resume` -- add (or decline) an
  agent-initiated invite on consent (``CONVERSATIONAL_INVITE``).

``_reread_approval_item`` is the shared resume-reread primitive; the
parked-context flow in ``_approval_review_gate`` imports it from here.
"""

import uuid
from typing import TYPE_CHECKING

from synthorg._core.features import require_service
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import agent_registry_of
from synthorg.meta.state import (
    MetaStateSlice,
    conversation_invite_repo_of,
    conversation_participant_repo_of,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_CONVERSATIONAL_EXECUTED,
    APPROVAL_GATE_CONVERSATIONAL_FAILED,
    APPROVAL_GATE_CONVERSATIONAL_NO_PROPOSAL,
    APPROVAL_GATE_CONVERSATIONAL_REJECTED,
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_INVITE_ACCEPTED,
    COS_GROUP_INVITE_DECLINED,
    COS_GROUP_INVITE_FAILED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.core.approval import ApprovalItem
    from synthorg.meta.chief_of_staff.group_models import ConversationInvite

logger = get_logger(__name__)


def _new_id() -> NotBlankStr:
    """Return a fresh opaque identifier.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(str(uuid.uuid4()))


async def _reread_approval_item(
    app_state: AppState,
    approval_id: str,
) -> ApprovalItem | None:
    """Re-read the just-decided approval, degrading to ``None`` on error.

    The decision is already persisted by the caller; a failed reread
    must not 500 the request. ``None`` routes the caller through the
    flow chain so each flow can apply its own ownership probe
    (Flow 0: yields to later flows; Flow 1: parked-context gate
    probe; Flow 2: review-gate is a no-op without ``task_id``).

    Returns:
        The ``ApprovalItem`` value when present, ``None`` otherwise.
    """
    try:
        store = require_service(
            app_state.slice(ApprovalStateSlice).store, "Approval Store"
        )
        return await store.get(approval_id)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="approval reread failed; falling back to parked-context probe",
        )
        return None


async def _load_conversational_proposal(
    app_state: AppState,
    approval_id: str,
) -> tuple[bool, object | None]:
    """Resolve a conversational-intake proposal for *approval_id*.

    Returns a (owns_decision, proposal_or_none) tuple:

    * ``(False, None)``: the approval is NOT a conversational-intake
      one (or the source could not be determined); the caller falls
      through to other resume flows. ``try_mid_execution_resume``
      owns the parked-context probe for the unreadable case, so
      yielding here is the safe default rather than raising and
      breaking that fallback.
    * ``(True, None)``: this flow owns the decision but there is no
      proposal row to act on (a logged no-op); the caller returns
      ``True`` without further work.
    * ``(True, proposal)``: this flow owns the decision and has a
      proposal to transition.

    Raises:
        ServiceUnavailableError: When the source is confirmed
            CONVERSATIONAL_INTAKE but the proposal repo is not wired.
            A hard misconfiguration -- swallowing it would silently
            strand a decided conversational approval.

    Returns:
        The ``tuple[bool, object]`` value when present, ``None`` otherwise.
    """
    from synthorg.core.enums import ApprovalSource  # noqa: PLC0415
    from synthorg.persistence.conversational_proposal_protocol import (  # noqa: PLC0415
        ConversationalProposalFilterSpec,
    )

    item = await _reread_approval_item(app_state, approval_id)
    if item is None or item.source is not ApprovalSource.CONVERSATIONAL_INTAKE:
        return False, None
    # This flow now owns the decision regardless of outcome.
    if app_state.slice(MetaStateSlice).conversational_proposal_repo is None:
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            note="conversational proposal repo not wired",
        )
        msg = "Conversational proposal repository unavailable"
        raise ServiceUnavailableError(msg)
    repo = require_service(
        app_state.slice(MetaStateSlice).conversational_proposal_repo,
        "Conversational Proposal Repository",
    )
    proposals = await repo.query(
        ConversationalProposalFilterSpec(approval_id=approval_id),
    )
    if not proposals:
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_NO_PROPOSAL,
            approval_id=approval_id,
        )
        return True, None
    return True, proposals[0]


async def _reject_conversational_proposal(
    app_state: AppState,
    approval_id: str,
    proposal: object,
) -> None:
    """CAS the proposal from PENDING to REJECTED; pipeline never runs."""
    from synthorg.core.enums import ConversationalProposalStatus  # noqa: PLC0415

    repo = require_service(
        app_state.slice(MetaStateSlice).conversational_proposal_repo,
        "Conversational Proposal Repository",
    )
    proposal_id = proposal.id  # type: ignore[attr-defined]
    transitioned = await repo.transition_if(
        proposal_id,
        ConversationalProposalStatus.PENDING,
        ConversationalProposalStatus.REJECTED,
    )
    if transitioned:
        logger.info(
            APPROVAL_GATE_CONVERSATIONAL_REJECTED,
            approval_id=approval_id,
            proposal_id=proposal_id,
        )
        return
    # Concurrent decision already transitioned this proposal (e.g.
    # duplicate approval-decision request). Surface the no-op so the
    # log doesn't claim a success we didn't make.
    logger.warning(
        APPROVAL_GATE_CONVERSATIONAL_FAILED,
        approval_id=approval_id,
        proposal_id=proposal_id,
        note="proposal already transitioned (reject path)",
    )


async def _execute_conversational_proposal(
    app_state: AppState,
    approval_id: str,
    proposal: object,
) -> None:
    """Acquire EXECUTING via CAS, run pipeline, finalize EXECUTED.

    On pipeline failure the EXECUTING state reverts to PENDING so
    the proposal stays retryable rather than locked in EXECUTING.

    Raises:
        ServiceUnavailableError: When the work pipeline is not wired
            at all (cannot run approved work without it).
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    from synthorg.core.enums import ConversationalProposalStatus  # noqa: PLC0415
    from synthorg.engine.pipeline.models import WorkItem  # noqa: PLC0415

    repo = require_service(
        app_state.slice(MetaStateSlice).conversational_proposal_repo,
        "Conversational Proposal Repository",
    )
    proposal_id = proposal.id  # type: ignore[attr-defined]
    work_item_json = proposal.work_item_json  # type: ignore[attr-defined]

    if app_state.slice(EngineStateSlice).work_pipeline is None:
        # Approved work can never run without a pipeline. Surface it
        # rather than marking the approval handled while the work is
        # silently stranded.
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            proposal_id=proposal_id,
            note="work pipeline not configured",
        )
        msg = "Work pipeline unavailable"
        raise ServiceUnavailableError(msg)

    # PENDING -> EXECUTING CAS first, so concurrent decisions cannot
    # both drive the pipeline for the same proposal. Only the winner
    # of this transition runs the pipeline; the loser returns without
    # side-effects.
    acquired = await repo.transition_if(
        proposal_id,
        ConversationalProposalStatus.PENDING,
        ConversationalProposalStatus.EXECUTING,
    )
    if not acquired:
        logger.warning(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            proposal_id=proposal_id,
            note="proposal already transitioned (execute-acquire path)",
        )
        return

    try:
        work_item = WorkItem.model_validate_json(work_item_json)
        work_pipeline = require_service(
            app_state.slice(EngineStateSlice).work_pipeline, "Work Pipeline"
        )
        await work_pipeline.run(work_item)
    except MemoryError, RecursionError:
        raise
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        # The approve decision is already persisted; a pipeline failure
        # must not 5xx the response. Revert EXECUTING -> PENDING so the
        # proposal is retryable rather than stuck in EXECUTING forever.
        reverted = await repo.transition_if(
            proposal_id,
            ConversationalProposalStatus.EXECUTING,
            ConversationalProposalStatus.PENDING,
        )
        log_exception_redacted(
            logger,
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            exc,
            approval_id=approval_id,
            proposal_id=proposal_id,
            note="pipeline run failed; proposal reverted to pending"
            if reverted
            else "pipeline run failed; proposal left in EXECUTING (revert lost race)",
        )
        return

    transitioned = await repo.transition_if(
        proposal_id,
        ConversationalProposalStatus.EXECUTING,
        ConversationalProposalStatus.EXECUTED,
    )
    if transitioned:
        logger.info(
            APPROVAL_GATE_CONVERSATIONAL_EXECUTED,
            approval_id=approval_id,
            proposal_id=proposal_id,
        )
        return
    # The acquire CAS guarantees we are the only caller that ran the
    # pipeline, so this only happens if a non-approval-gate writer
    # mutated the proposal mid-flight (operator override, future
    # admin endpoint). Surface the no-op so the log doesn't claim a
    # CAS success we didn't make.
    logger.warning(
        APPROVAL_GATE_CONVERSATIONAL_FAILED,
        approval_id=approval_id,
        proposal_id=proposal_id,
        note=(
            "proposal mutated mid-execute (executing->executed CAS "
            "failed); pipeline run still succeeded"
        ),
    )


async def try_conversational_intake_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
) -> bool:
    """Run a decided conversational-intake proposal, if this is one.

    Deterministic routing off the persisted
    :attr:`ApprovalItem.source` discriminator: only
    ``CONVERSATIONAL_INTAKE`` approvals are owned here; everything else
    returns ``False`` so the caller falls through to the parked-context
    / review-gate flows. Once owned, the decision is fully resolved on
    this path and ``True`` is returned even on failure so the approval
    is never double-handled.

    On approval the parked ``WorkItem`` is rebuilt from the proposal
    and driven through the work pipeline (still gated -- it only runs
    because a human approved); the proposal is then marked EXECUTED. On
    rejection the proposal is marked REJECTED and the pipeline is never
    touched.

    Raises:
        ServiceUnavailableError: When the approval store read fails,
            the proposal repo is not wired, or the work pipeline is
            missing on approve. All three are hard misconfigurations
            the operator must fix.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    owns_decision, proposal = await _load_conversational_proposal(
        app_state, approval_id
    )
    if not owns_decision:
        return False
    if proposal is None:
        return True
    if not approved:
        await _reject_conversational_proposal(app_state, approval_id, proposal)
        return True
    await _execute_conversational_proposal(app_state, approval_id, proposal)
    return True


async def _load_conversation_invite(
    app_state: AppState,
    approval_id: str,
) -> tuple[bool, ConversationInvite | None]:
    """Resolve an agent-initiated invite for *approval_id*.

    Mirrors :func:`_load_conversational_proposal`. Returns a
    ``(owns_decision, invite_or_none)`` tuple: ``(False, None)`` when the
    approval is not a ``CONVERSATIONAL_INVITE`` one (fall through),
    ``(True, None)`` when this flow owns the decision but no invite row
    backs it (a logged no-op), ``(True, invite)`` otherwise.

    Raises:
        ServiceUnavailableError: When the source is confirmed
            CONVERSATIONAL_INVITE but the invite repo is not wired.

    Returns:
        The ``(owns_decision, invite)`` pair.
    """
    from synthorg.core.enums import ApprovalSource  # noqa: PLC0415
    from synthorg.persistence.conversation_invite_protocol import (  # noqa: PLC0415
        ConversationInviteFilterSpec,
    )

    item = await _reread_approval_item(app_state, approval_id)
    if item is None or item.source is not ApprovalSource.CONVERSATIONAL_INVITE:
        return False, None
    if app_state.slice(MetaStateSlice).conversation_invite_repo is None:
        logger.error(
            COS_GROUP_INVITE_FAILED,
            approval_id=approval_id,
            note="conversation invite repo not wired",
        )
        msg = "Conversation invite repository unavailable"
        raise ServiceUnavailableError(msg)
    repo = conversation_invite_repo_of(app_state)
    invites = await repo.query(ConversationInviteFilterSpec(approval_id=approval_id))
    if not invites:
        logger.error(
            COS_GROUP_INVITE_FAILED,
            approval_id=approval_id,
            note="no invite row backs this consent approval",
        )
        return True, None
    return True, invites[0]


async def _decline_invite(
    app_state: AppState,
    approval_id: str,
    invite: ConversationInvite,
) -> None:
    """CAS the invite PENDING -> DECLINED; membership is left unchanged."""
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationInviteStatus,
    )

    repo = conversation_invite_repo_of(app_state)
    transitioned = await repo.transition_if(
        invite.id,
        ConversationInviteStatus.PENDING,
        ConversationInviteStatus.DECLINED,
    )
    if transitioned:
        logger.info(
            COS_GROUP_INVITE_DECLINED,
            approval_id=approval_id,
            invite_id=invite.id,
            conversation_id=invite.conversation_id,
        )
        return
    logger.warning(
        COS_GROUP_INVITE_FAILED,
        approval_id=approval_id,
        invite_id=invite.id,
        note="invite already transitioned (decline path)",
    )


async def _accept_invite(
    app_state: AppState,
    approval_id: str,
    invite: ConversationInvite,
    decided_by: str,
) -> None:
    """CAS the invite to ACCEPTED, then add the invited agent's roster row.

    The CAS is the single-winner consent gate: only one approve
    transitions PENDING -> ACCEPTED, so a duplicate decision is a safe
    no-op. The participant insert is idempotent (skipped when the target
    is already active); on insert failure the CAS is reverted to PENDING
    so the consent stays retryable, mirroring
    :func:`_execute_conversational_proposal`.

    Raises:
        ServiceUnavailableError: When the invite / participant repos are
            not wired (a hard misconfiguration).
    """
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationInviteStatus,
    )

    invite_repo = conversation_invite_repo_of(app_state)
    acquired = await invite_repo.transition_if(
        invite.id,
        ConversationInviteStatus.PENDING,
        ConversationInviteStatus.ACCEPTED,
    )
    if not acquired:
        logger.warning(
            COS_GROUP_INVITE_FAILED,
            approval_id=approval_id,
            invite_id=invite.id,
            note="invite already transitioned (accept-acquire path)",
        )
        return
    try:
        await _add_invited_participant(app_state, invite, decided_by)
    except Exception as exc:
        reraise_critical(exc)
        reverted = await invite_repo.transition_if(
            invite.id,
            ConversationInviteStatus.ACCEPTED,
            ConversationInviteStatus.PENDING,
        )
        log_exception_redacted(
            logger,
            COS_GROUP_INVITE_FAILED,
            exc,
            approval_id=approval_id,
            invite_id=invite.id,
            note="participant add failed; invite reverted to pending"
            if reverted
            else "participant add failed; invite left ACCEPTED (revert lost race)",
        )
        return
    logger.info(
        COS_GROUP_INVITE_ACCEPTED,
        approval_id=approval_id,
        invite_id=invite.id,
        conversation_id=invite.conversation_id,
        target_agent_id=invite.target_agent_id,
    )


async def _add_invited_participant(
    app_state: AppState,
    invite: ConversationInvite,
    decided_by: str,
) -> None:
    """Insert the invited agent's active roster row, idempotently.

    Resolves ``target_agent_id`` to its current identity (the invite row
    carries no agent name -- the single migration shipped without one),
    so a target deleted between park and consent is a logged no-op
    rather than a crash. A target already active in the roster is also a
    no-op (the table's unique ``(conversation_id, agent_id)`` constraint
    is the backstop).
    """
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationParticipantStatus,
    )
    from synthorg.meta.chief_of_staff.group_models import (  # noqa: PLC0415
        ConversationParticipant,
    )
    from synthorg.persistence.conversation_participant_protocol import (  # noqa: PLC0415
        ConversationParticipantFilterSpec,
    )

    identity = await agent_registry_of(app_state).get(invite.target_agent_id)
    if identity is None:
        logger.warning(
            COS_GROUP_INVITE_FAILED,
            invite_id=invite.id,
            conversation_id=invite.conversation_id,
            target_agent_id=invite.target_agent_id,
            note="invited agent no longer registered; roster row not added",
        )
        return
    participant_repo = conversation_participant_repo_of(app_state)
    roster = await participant_repo.query(
        ConversationParticipantFilterSpec(
            conversation_id=invite.conversation_id,
            status=ConversationParticipantStatus.ACTIVE,
        )
    )
    if any(p.agent_id == invite.target_agent_id for p in roster):
        return
    await participant_repo.save(
        ConversationParticipant(
            id=_new_id(),
            conversation_id=invite.conversation_id,
            agent_id=invite.target_agent_id,
            agent_name=identity.name,
            participant_role=identity.role,
            status=ConversationParticipantStatus.ACTIVE,
            added_by=NotBlankStr(decided_by),
            added_at=app_state.clock.now(),
        )
    )


async def try_conversational_invite_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str | None = None,
) -> bool:
    """Resolve a decided agent-initiated invite, if this is one.

    Deterministic routing off ``ApprovalItem.source``: only
    ``CONVERSATIONAL_INVITE`` approvals are owned here; everything else
    returns ``False`` to fall through to the parked-context / review-gate
    flows. Repo-direct + ungated, so the consent decision resolves even
    after the invite feature is toggled off. Once owned, ``True`` is
    returned even on failure so the approval is never double-handled.

    Returns:
        ``True`` when this flow owns the decision, ``False`` otherwise.
    """
    owns_decision, invite = await _load_conversation_invite(app_state, approval_id)
    if not owns_decision:
        return False
    if invite is None:
        return True
    if not approved:
        await _decline_invite(app_state, approval_id, invite)
        return True
    await _accept_invite(app_state, approval_id, invite, resolve_decided_by(decided_by))
    return True


__all__ = [
    "try_conversational_intake_resume",
    "try_conversational_invite_resume",
]
