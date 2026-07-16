# module-kind: orchestrator
"""Conversational approval-resume flows for the approvals controller.

Kept separate from ``_approval_review_gate.py`` (which holds the
parked-context + review-gate flows and the ``signal_resume_intent``
dispatcher) so each resume concern stays within its module-size tier;
the agent-invite flow and the conversational-steering flow are distinct
resume surfaces that share only the reread primitive below.

Two flows live here, both keyed off the persisted
:attr:`ApprovalItem.source` discriminator and both repo-direct (never
through the gated feature services), so a decided approval resolves
even after its feature is toggled off:

- :func:`try_conversational_intake_resume` -- issue (or drop) a decided
  conversational steering directive (``CONVERSATIONAL_INTAKE``). A work
  brief no longer parks here; it drafts a plan into Plan Review instead.
- :func:`try_conversational_invite_resume` -- add (or decline) an
  agent-initiated invite on consent (``CONVERSATIONAL_INVITE``).

``_reread_approval_item`` is the shared resume-reread primitive; the
parked-context flow in ``_approval_review_gate`` imports it from here.
"""

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.hr.state import agent_registry_of
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.meta.state import MetaStateSlice, conversational_resume_service_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_INVITE_ACCEPTED,
    COS_GROUP_INVITE_DECLINED,
    COS_GROUP_INVITE_FAILED,
    COS_GROUP_PARTICIPANTS_ADDED,
)

logger = get_logger(__name__)


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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="approval reread failed; falling back to parked-context probe",
        )
        return None


async def try_conversational_intake_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
) -> bool:
    """Resolve a decided conversational steering directive, if this is one.

    The only ``CONVERSATIONAL_INTAKE`` approval the proposer still parks is
    a steering directive (a work brief drafts a plan into Plan Review
    instead of parking here). A steering directive rides in the approval
    metadata, not a proposal row: on approval it issues to the steering
    service, on rejection it is a no-op. Everything else returns ``False``
    so the caller falls through to the parked-context / review-gate flows.

    Returns:
        ``True`` when this flow owns the decision (a steering directive),
        ``False`` otherwise.
    """
    from synthorg.meta.chief_of_staff._intake_parking import (  # noqa: PLC0415
        resume_conversational_steering,
    )

    item = await _reread_approval_item(app_state, approval_id)
    return await resume_conversational_steering(app_state, item, approved=approved)


async def _load_conversation_invite(
    app_state: AppState,
    approval_id: str,
) -> tuple[bool, ConversationInvite | None]:
    """Resolve an agent-initiated invite for *approval_id*.

    Returns a ``(owns_decision, invite_or_none)`` tuple: ``(False, None)``
    when the approval is not a ``CONVERSATIONAL_INVITE`` one (fall
    through), ``(True, None)`` when this flow owns the decision but no
    invite row backs it (a logged no-op), ``(True, invite)`` otherwise.

    Raises:
        ServiceUnavailableError: When the source is confirmed
            CONVERSATIONAL_INVITE but the invite repo is not wired.

    Returns:
        The ``(owns_decision, invite)`` pair.
    """
    from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415

    item = await _reread_approval_item(app_state, approval_id)
    if item is None or item.source is not ApprovalSource.CONVERSATIONAL_INVITE:
        return False, None
    service = app_state.slice(MetaStateSlice).conversational_resume_service
    if service is None:
        logger.error(
            COS_GROUP_INVITE_FAILED,
            approval_id=approval_id,
            note="conversational resume service not wired",
        )
        msg = "Conversational resume service unavailable"
        raise ServiceUnavailableError(msg)
    invites = await service.invites_for_approval(approval_id)
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

    service = conversational_resume_service_of(app_state)
    transitioned = await service.transition_invite(
        str(invite.id),
        from_status=ConversationInviteStatus.PENDING,
        to_status=ConversationInviteStatus.DECLINED,
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
    so the consent stays retryable.

    Raises:
        ServiceUnavailableError: When the invite / participant repos are
            not wired (a hard misconfiguration).
    """
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationInviteStatus,
    )

    service = conversational_resume_service_of(app_state)
    acquired = await service.transition_invite(
        str(invite.id),
        from_status=ConversationInviteStatus.PENDING,
        to_status=ConversationInviteStatus.ACCEPTED,
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        reverted = await service.transition_invite(
            str(invite.id),
            from_status=ConversationInviteStatus.ACCEPTED,
            to_status=ConversationInviteStatus.PENDING,
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
    stores no agent name, so the display name is read from the registry
    at accept time), so a target deleted between park and consent is a
    logged no-op rather than a crash. A target already active in the
    roster is also a no-op. The participant cap is re-checked here as
    well as at park time: two invites for different agents can both pass
    the park-time guard against the same pre-round roster, so without
    this accept-time check two approvals could push the roster one over
    ``group_chat_max_participants``. The table's unique
    ``(conversation_id, agent_id)`` constraint is the duplicate-agent
    backstop; this guard is the total-count one.
    """
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ConversationParticipantStatus,
    )
    from synthorg.meta.chief_of_staff.group_models import (  # noqa: PLC0415
        ConversationParticipant,
    )
    from synthorg.meta.state import self_improvement_config_of  # noqa: PLC0415

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
    from synthorg.meta.chief_of_staff.enums import (  # noqa: PLC0415
        ParticipantAdmission,
    )

    service = conversational_resume_service_of(app_state)
    meta_config = await self_improvement_config_of(app_state)
    cap = meta_config.chief_of_staff.group_chat_max_participants
    # Atomic admit-under-cap: the duplicate check, the active-count read,
    # and the insert run in one backend transaction, so two concurrent
    # consents for different agents cannot both pass the cap and push the
    # roster to ``cap + 1`` (the prior read-then-write here raced).
    outcome = await service.admit_participant_within_cap(
        ConversationParticipant(
            conversation_id=invite.conversation_id,
            agent_id=invite.target_agent_id,
            agent_name=identity.name,
            participant_role=identity.role,
            status=ConversationParticipantStatus.ACTIVE,
            added_by=NotBlankStr(decided_by),
            added_at=app_state.clock.now(),
        ),
        cap=cap,
    )
    if outcome is ParticipantAdmission.ALREADY_ACTIVE:
        return
    if outcome is ParticipantAdmission.CAP_REACHED:
        logger.warning(
            COS_GROUP_INVITE_FAILED,
            invite_id=invite.id,
            conversation_id=invite.conversation_id,
            target_agent_id=invite.target_agent_id,
            note="participant cap reached at accept; roster row not added",
        )
        return
    logger.info(
        COS_GROUP_PARTICIPANTS_ADDED,
        invite_id=invite.id,
        conversation_id=invite.conversation_id,
        agent_id=invite.target_agent_id,
        added_by=decided_by,
        note="invited agent enrolled in group conversation after consent",
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
