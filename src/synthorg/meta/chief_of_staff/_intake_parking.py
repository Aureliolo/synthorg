"""Conversational steering parking + execution helpers.

Free functions that compose and park the one conversational approval type
the Chief of Staff proposer produces (a steering directive), and that
execute an approved steering directive at the approval gate. Starting work
is not among them: that happens only through the charter interview and the
operator's approval of what it drafts. Kept separate from ``propose.py``
and the approval-gate module so each concern stays within its module-size
tier.

A steering directive carries no proposal row: it rides in the approval
``metadata`` (the ``STEERING_INTAKE_*`` keys), so the gate reads it back
on approval and routes it to ``SteeringService.issue``.
"""

import uuid
from datetime import datetime
from typing import TypeGuard

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.models import (
    STEERING_INTAKE_KIND_KEY,
    STEERING_INTAKE_PROJECT_KEY,
    STEERING_INTAKE_TEXT_KEY,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ProposeArgs,
    ProposedSteering,
    SteeringProposalSummary,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_CONVERSATIONAL_EXECUTED,
)
from synthorg.observability.events.chief_of_staff import (
    COS_PROPOSE_FAILED,
)

logger = get_logger(__name__)

_STEERING_ACTION_TYPE: NotBlankStr = NotBlankStr("conversational:steer")


def _new_approval_id() -> NotBlankStr:
    """Return a fresh opaque approval identifier.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(str(uuid.uuid4()))


async def park_steering(
    *,
    approval_store: ApprovalStoreProtocol,
    conversation: Conversation,
    args: ProposeArgs,
    steer: ProposedSteering,
    project: NotBlankStr,
    config: ChiefOfStaffConfig,
    now: datetime,
) -> SteeringProposalSummary:
    """Publish the gating approval for one steering directive.

    The directive rides in the approval ``metadata`` (no proposal row), so the
    only compensation needed is deleting the approval.

    Returns:
        ``SteeringProposalSummary`` instance.
    """
    approval_id = _new_approval_id()
    await approval_store.add(
        ApprovalItem(
            id=uuid.UUID(approval_id),
            action_type=_STEERING_ACTION_TYPE,
            title=NotBlankStr(f"Steer {project}: {steer.kind.value}"),
            description=steer.text,
            requested_by=args.created_by,
            risk_level=config.propose_default_risk_level,
            source=ApprovalSource.CONVERSATIONAL_INTAKE,
            status=ApprovalStatus.PENDING,
            created_at=now,
            metadata={
                "conversation_id": str(conversation.id),
                STEERING_INTAKE_KIND_KEY: steer.kind.value,
                STEERING_INTAKE_PROJECT_KEY: project,
                STEERING_INTAKE_TEXT_KEY: steer.text,
            },
        )
    )
    return SteeringProposalSummary(
        approval_id=approval_id,
        kind=steer.kind,
        text=steer.text,
        project=project,
    )


async def unwind_parked_steering(
    approval_store: ApprovalStoreProtocol,
    approval_id: NotBlankStr,
) -> None:
    """Remove a previously-parked steering approval (compensation).

    Best-effort: logged but never re-raises so the caller's original exception
    is the one operators see.
    """
    try:
        await approval_store.delete(approval_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            COS_PROPOSE_FAILED,
            detail="unwind_steering_failed",
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def is_conversational_steering(item: ApprovalItem | None) -> TypeGuard[ApprovalItem]:
    """Whether *item* is a parked conversational steering directive.

    Narrows ``item`` to a non-``None`` :class:`ApprovalItem` for callers.

    Returns:
        ``True`` when the approval is a ``CONVERSATIONAL_INTAKE`` item carrying
        the steering-directive metadata marker.
    """
    return (
        item is not None
        and item.source is ApprovalSource.CONVERSATIONAL_INTAKE
        and STEERING_INTAKE_KIND_KEY in item.metadata
        and STEERING_INTAKE_PROJECT_KEY in item.metadata
        and STEERING_INTAKE_TEXT_KEY in item.metadata
    )


async def execute_conversational_steering(
    app_state: AppState,
    item: ApprovalItem,
) -> None:
    """Route an approved conversational steering directive to the steering service.

    Supersession is not part of the conversational path; the operator supersedes
    explicitly at the cockpit.

    Raises:
        ServiceUnavailableError: When the steering service is not wired; an
            approved directive that cannot execute is a hard misconfiguration.
    """
    from synthorg.engine.cockpit.state import CockpitStateSlice  # noqa: PLC0415

    steering = require_service(
        app_state.slice(CockpitStateSlice).steering_service, "Steering Service"
    )
    result = await steering.issue(
        project_id=NotBlankStr(item.metadata[STEERING_INTAKE_PROJECT_KEY]),
        kind=InterventionKind(item.metadata[STEERING_INTAKE_KIND_KEY]),
        text=NotBlankStr(item.metadata[STEERING_INTAKE_TEXT_KEY]),
        author=NotBlankStr(item.requested_by),
    )
    logger.info(
        APPROVAL_GATE_CONVERSATIONAL_EXECUTED,
        approval_id=item.id,
        directive_id=result.directive_id,
        note="conversational steering directive issued",
    )


async def resume_conversational_steering(
    app_state: AppState,
    item: ApprovalItem | None,
    *,
    approved: bool,
) -> bool:
    """Resolve a decided conversational steering approval, if this is one.

    Returns:
        ``True`` when *item* is a steering directive (owned here): on approval
        it issues, on rejection it is a no-op. ``False`` when *item* is not a
        steering directive, so the caller falls through.
    """
    if not is_conversational_steering(item):
        return False
    if approved:
        await execute_conversational_steering(app_state, item)
    return True
