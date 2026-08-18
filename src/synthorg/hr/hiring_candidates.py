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
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.plan import PlanOption
from synthorg.core.role import Skill
from synthorg.core.types import NotBlankStr, stable_agent_id
from synthorg.hr.enums import AgentStatus
from synthorg.hr.errors import HiringError, InvalidCandidateError
from synthorg.hr.hire_model_proposal import HireModelProposal
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_RISK_TIER_MISSING,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.risk_map import MapBackedRiskClassifier, default_risk_classifier

# What a candidate is expected to cost per month when the request named no
# budget. It is an estimate shown to the approving human, never a limit
# enforced anywhere, so a request that omits a ceiling still presents a
# number rather than a blank.
_UNSPECIFIED_MONTHLY_COST_ESTIMATE: Final[float] = 50.0

#: Below this many pairs there is no fork to offer: one option is not a
#: choice, and the decision invariant refuses a package carrying fewer.
_MIN_DECISION_OPTIONS: Final[int] = 2

logger = get_logger(__name__)

#: Classifier for the one action this module raises an approval on. Built
#: over the shared taxonomy so a hire is scored the same way every other
#: action is, and so an unmapped type fails safe to HIGH rather than raising.
_HIRE_RISK_CLASSIFIER: Final[MapBackedRiskClassifier] = default_risk_classifier(
    miss_event=HR_HIRING_RISK_TIER_MISSING,
)


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


def hire_decision_brief(
    request: HiringRequest,
    candidate: CandidateCard,
    *,
    proposal: HireModelProposal,
) -> str:
    """Say what approving this hire actually commits the organisation to.

    An operator asked to approve one of these could previously read a job
    title, a sentence saying the role was unstaffed, and two raw UUIDs. Every
    fact the decision turns on was held somewhere else: which team the agent
    joins, what it claims to be able to do, what it is expected to cost, and
    above all what model it would run on.

    That last one is why this is not cosmetic. The pair decides what the agent
    can do and what it costs for as long as it exists, and a hire with no pair
    is REFUSED after approval, so the operator was being asked for a decision
    the system could not then carry out.

    Args:
        request: The hiring request.
        candidate: The candidate being proposed.
        proposal: The pairs offered for this hire, or an empty proposal
            carrying why there are none.

    Returns:
        The description an operator decides on.
    """
    lines = [
        request.reason,
        "",
        f"Team: {candidate.department}",
    ]
    if candidate.skills:
        lines.append(f"Claims: {', '.join(skill.name for skill in candidate.skills)}")
    lines.append(f"Estimated cost: {candidate.estimated_monthly_cost:g} per month")
    recommended = proposal.recommended
    if recommended is None:
        lines.append(
            f"Model: NONE AVAILABLE. {proposal.unmatched_reason} Approving "
            "this would refuse the hire rather than register an agent that "
            "fails every dispatch."
        )
    else:
        lines.append(f"Model: {recommended.label} ({recommended.summary})")
        if len(proposal.options) > 1:
            lines.append(
                "Pick a different option below to hire onto another model instead."
            )
    return "\n".join(lines)


def _hire_decision_options(proposal: HireModelProposal) -> tuple[PlanOption, ...]:
    """Turn the offered pairs into the fork the operator picks from.

    Empty below two options, which is the decision invariant and also the
    honest shape: one option is not a choice, and offering it as one would ask
    the operator to confirm a pair they cannot change here.

    Returns:
        One option per offered pair, the org's own profile recommended.
    """
    if len(proposal.options) < _MIN_DECISION_OPTIONS:
        return ()
    recommended = proposal.recommended
    return tuple(
        PlanOption(
            # The serialised pair, so the operator's pick decodes straight
            # back to the binding it named with no lookup table in between.
            id=NotBlankStr(option.option_id),
            title=NotBlankStr(option.label),
            summary=NotBlankStr(option.summary),
            recommended=option is recommended,
        )
        for option in proposal.options
    )


def build_hire_approval_item(
    request: HiringRequest,
    candidate: CandidateCard,
    *,
    candidate_id: str,
    approval_id: str,
    proposal: HireModelProposal | None = None,
) -> ApprovalItem:
    """Build the approval item a human decides a hire on.

    Args:
        request: The hiring request.
        candidate: The candidate being proposed.
        candidate_id: ID of the candidate, carried in the metadata so the
            decision handler can find its way back to the request.
        approval_id: Pre-minted item ID, stamped onto the request too.
        proposal: The pairs this hire could run on. Shown either way, because
            approving with nothing available is refused, and offered as a
            decision fork when there is more than one so the operator can bind
            a different model without leaving the approval.

    Returns:
        The approval item to store.
    """
    offered = proposal if proposal is not None else HireModelProposal()
    description = hire_decision_brief(request, candidate, proposal=offered)
    return ApprovalItem(
        id=UUID(approval_id),
        action_type=NotBlankStr(ActionType.ORG_HIRE),
        title=NotBlankStr(f"Hire {candidate.name} as {candidate.role}"),
        description=NotBlankStr(description),
        requested_by=request.requested_by,
        # Classified rather than indexed: the map is the taxonomy, but the
        # classifier is what applies an operator's overrides on top of it and
        # what fails safe to HIGH for a type the map does not name. Reading
        # the map directly would ignore the first and raise KeyError for the
        # second, which is the loudest possible way to get the quiet answer.
        risk_level=_HIRE_RISK_CLASSIFIER.classify(ActionType.ORG_HIRE.value),
        created_at=datetime.now(UTC),
        metadata={"request_id": str(request.id), "candidate_id": candidate_id},
        evidence_package=_hire_evidence(
            request,
            candidate,
            approval_id=approval_id,
            description=description,
            proposal=offered,
        ),
    )


def _hire_evidence(
    request: HiringRequest,
    candidate: CandidateCard,
    *,
    approval_id: str,
    description: str,
    proposal: HireModelProposal,
) -> EvidencePackage | None:
    """Carry the model fork, when there is one to offer.

    ``None`` when fewer than two pairs are available: the approval is then a
    plain yes/no on the recommended pair (or on nothing at all), and an
    evidence package with no options adds a second empty panel to the drawer.

    Returns:
        The evidence package holding the fork, or ``None``.
    """
    options = _hire_decision_options(proposal)
    if not options:
        return None
    return EvidencePackage(
        id=NotBlankStr(approval_id),
        created_at=datetime.now(UTC),
        title=NotBlankStr(f"Model for {candidate.role}"),
        narrative=NotBlankStr(description),
        recommended_actions=(
            RecommendedAction(
                action_type=NotBlankStr("approve"),
                label=NotBlankStr("Hire on the selected model"),
                description=NotBlankStr(
                    "Registers the agent bound to the pair selected above."
                ),
            ),
        ),
        options=options,
        # Whoever asked for the hire, which is the reconciler for a park it
        # opened and the operator for a manual one. The package is a statement
        # about their request, not something an agent produced.
        source_agent_id=request.requested_by,
        risk_level=_HIRE_RISK_CLASSIFIER.classify(ActionType.ORG_HIRE.value),
        metadata={"request_id": str(request.id)},
    )


def hire_agent_id(request: HiringRequest, candidate: CandidateCard) -> UUID:
    """Derive the roster id an approved hire registers under.

    Seeded with the REQUEST id, not the candidate name alone. A candidate is
    named from its role and department, both of which the reconciler reads
    from the catalogue, so every hire it ever opens for a role would otherwise
    mint the same id. That is only invisible while nobody has held the role
    before: a terminated predecessor stays on the register under exactly that
    id, so the next approved hire collides with it and can never land, for as
    long as the org exists.

    Stable per request, so retrying an interrupted instantiation rebuilds the
    same id rather than registering a second agent for one approval.

    Args:
        request: The approved request being instantiated.
        candidate: The candidate it selected.

    Returns:
        The deterministic id for this request's hire.
    """
    return stable_agent_id(f"{candidate.name}:{request.id}")


def build_agent_identity(
    candidate: CandidateCard,
    *,
    request: HiringRequest,
    model: ModelConfig,
    status: AgentStatus,
) -> AgentIdentity:
    """Build the roster identity an approved candidate becomes.

    Args:
        candidate: The approved candidate.
        request: The request being instantiated, which seeds the agent id.
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
            id=hire_agent_id(request, candidate),
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
    "hire_agent_id",
    "select_candidate",
]
