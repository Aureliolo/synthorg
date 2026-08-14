# module-kind: code
"""Turn a hiring request into the objects the pipeline hands onward.

A candidate card, the approval item a human decides, and finally the roster
identity an approved hire becomes. All construction, no lifecycle: the
pipeline's ordering, locking and persistence stay in ``hiring_service``.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.approval import ApprovalItem
from synthorg.core.role import Skill
from synthorg.core.types import NotBlankStr, stable_agent_id
from synthorg.hr.enums import AgentStatus
from synthorg.hr.errors import HiringError, InvalidCandidateError
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_HIRING_INSTANTIATION_FAILED
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.risk_map import DEFAULT_RISK_MAP

# What a candidate is expected to cost per month when the request named no
# budget. It is an estimate shown to the approving human, never a limit
# enforced anywhere, so a request that omits a ceiling still presents a
# number rather than a blank.
_UNSPECIFIED_MONTHLY_COST_ESTIMATE: Final[float] = 50.0

logger = get_logger(__name__)


def build_candidate(request: HiringRequest) -> CandidateCard:
    """Build a candidate card from a hiring request's role defaults.

    Args:
        request: The hiring request to generate a candidate for.

    Returns:
        A new ``CandidateCard``.
    """
    return CandidateCard(
        name=NotBlankStr(f"{request.role}-{request.department}-agent"),
        role=request.role,
        department=request.department,
        skills=tuple(Skill(id=s, name=s) for s in request.required_skills),
        rationale=NotBlankStr(f"Generated for: {request.reason}"),
        estimated_monthly_cost=(
            request.budget_limit_monthly
            if request.budget_limit_monthly is not None
            else _UNSPECIFIED_MONTHLY_COST_ESTIMATE
        ),
        template_source=request.template_name,
    )


def select_candidate(request: HiringRequest) -> CandidateCard:
    """Find the candidate the request was approved for.

    Args:
        request: The hiring request.

    Returns:
        The selected candidate card.

    Raises:
        InvalidCandidateError: If the selected candidate is not found.
    """
    candidate = next(
        (c for c in request.candidates if str(c.id) == request.selected_candidate_id),
        None,
    )
    if candidate is None:
        msg = (
            f"Selected candidate {request.selected_candidate_id!r} "
            f"not found on request {request.id!r}"
        )
        logger.warning(
            HR_HIRING_INSTANTIATION_FAILED,
            request_id=str(request.id),
            error=msg,
        )
        raise InvalidCandidateError(msg)
    return candidate


def build_hire_approval_item(
    request: HiringRequest,
    candidate: CandidateCard,
    *,
    candidate_id: str,
    approval_id: str,
) -> ApprovalItem:
    """Build the approval item a human decides a hire on.

    Args:
        request: The hiring request.
        candidate: The candidate being proposed.
        candidate_id: ID of the candidate, carried in the metadata so the
            decision handler can find its way back to the request.
        approval_id: Pre-minted item ID, stamped onto the request too.

    Returns:
        The approval item to store.
    """
    return ApprovalItem(
        id=UUID(approval_id),
        action_type=NotBlankStr(ActionType.ORG_HIRE),
        title=NotBlankStr(f"Hire {candidate.name} as {candidate.role}"),
        description=NotBlankStr(request.reason),
        requested_by=request.requested_by,
        # Read from the risk taxonomy rather than asserted here: two owners
        # for one action's risk means the quieter one is wrong, and this one
        # disagreed with the map it was meant to reflect.
        risk_level=DEFAULT_RISK_MAP[ActionType.ORG_HIRE.value],
        created_at=datetime.now(UTC),
        metadata={"request_id": str(request.id), "candidate_id": candidate_id},
    )


def build_agent_identity(
    candidate: CandidateCard,
    *,
    model: ModelConfig,
    status: AgentStatus,
) -> AgentIdentity:
    """Build the roster identity an approved candidate becomes.

    Args:
        candidate: The approved candidate.
        model: The pair the new agent is bound to. Required, and required to
            be a real one: an agent registered against a placeholder provider
            joins the roster looking staffed and fails every dispatch.
        status: Lifecycle status to start in.

    Returns:
        A new agent identity.

    Raises:
        HiringError: If the identity cannot be constructed.
    """
    try:
        return AgentIdentity(
            id=stable_agent_id(candidate.name),
            name=candidate.name,
            role=candidate.role,
            department=candidate.department,
            skills=SkillSet(primary=candidate.skills),
            model=model,
            status=status,
            hiring_date=datetime.now(UTC).date(),
        )
    except (ValidationError, ValueError) as exc:
        msg = f"Failed to construct AgentIdentity for candidate {candidate.id!r}"
        logger.warning(
            HR_HIRING_INSTANTIATION_FAILED,
            candidate_id=str(candidate.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise HiringError(msg) from exc


__all__ = [
    "build_agent_identity",
    "build_candidate",
    "build_hire_approval_item",
    "select_candidate",
]
