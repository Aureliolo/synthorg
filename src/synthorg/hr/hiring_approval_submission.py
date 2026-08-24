# module-kind: code
"""Raising the approval a human decides a hire on.

The step between "a candidate exists" and "somebody has been asked": propose
the pairs the hire could run on, and turn them plus the candidate into the
item an operator reads. Free functions taking the collaborators each needs, so
the hiring service keeps the decision flow and the durable request state, in
the same shape as ``hiring_instantiation`` keeps the roster-touching half.
"""

from uuid import uuid4

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import HiringError
from synthorg.hr.hire_model_proposal import (
    HireModelProposal,
    ProviderCatalogue,
    propose_hire_models,
)
from synthorg.hr.hiring_candidates import build_hire_approval_item
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_HIRING_APPROVAL_ORPHANED,
    HR_HIRING_MODEL_PROPOSED,
    HR_HIRING_MODEL_UNSET,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: The spend profile that biases nothing, used when the company's own cannot
#: be read. It decides which proposed pair is RECOMMENDED and nothing else, so
#: falling back here costs the operator a default, never an option.
_NEUTRAL_SPEND_PROFILE: NotBlankStr = NotBlankStr("balanced")


async def propose_models(
    candidate: CandidateCard,
    *,
    catalogue: ProviderCatalogue | None,
    resolver: ConfigResolverProtocol | None,
) -> HireModelProposal:
    """Offer the pairs this candidate could be hired onto.

    Args:
        candidate: The candidate being proposed.
        catalogue: The operator's configured providers.
        resolver: Reads the company's model-spend profile, which decides which
            offered pair is recommended.

    Returns:
        The proposal, empty and carrying its reason when nothing matched.
    """
    return await propose_hire_models(
        candidate,
        catalogue=catalogue,
        org_profile=await _spend_profile(resolver),
    )


async def _spend_profile(resolver: ConfigResolverProtocol | None) -> str:
    """Read the company's model-spend profile.

    Returns:
        The configured profile, or the neutral one when it cannot be read.
        A failure costs the operator a default rather than the proposal: the
        profile only decides which option is recommended, and every option is
        still theirs to pick.
    """
    if resolver is None:
        return _NEUTRAL_SPEND_PROFILE
    try:
        return await resolver.get_str("company", "model_spend_profile")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # Broad on purpose: the settings read reaches a database, so a socket
        # timeout or a dropped connection arrives as an ordinary exception,
        # and narrowing to DomainError let one of those abort the whole hire
        # approval over the field that decides only which option is starred.
        reraise_critical(exc)
        logger.warning(
            HR_HIRING_MODEL_PROPOSED,
            note="model spend profile unreadable; the neutral one stands",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _NEUTRAL_SPEND_PROFILE


def recommended_ref(proposal: HireModelProposal) -> NotBlankStr | None:
    """The binding a hire takes when nobody picks a different one.

    Returns:
        The recommended pair in canonical MODEL_REF form, or ``None`` when
        nothing was proposable.
    """
    recommended = proposal.recommended
    return NotBlankStr(recommended.option_id) if recommended is not None else None


def build_approval(
    request: HiringRequest,
    candidate: CandidateCard,
    *,
    candidate_id: str,
    approval_id: str,
    proposal: HireModelProposal,
) -> ApprovalItem:
    """Build the item an operator decides this hire on.

    Args:
        request: The hiring request.
        candidate: The candidate being proposed.
        candidate_id: ID of the candidate, carried in the metadata so the
            decision handler can find its way back to the request.
        approval_id: Pre-minted item ID, stamped onto the request too.
        proposal: The pairs the hire could run on, offered as a fork the
            operator can override without leaving the approval.

    Returns:
        The approval item to store.
    """
    return build_hire_approval_item(
        request,
        candidate,
        candidate_id=candidate_id,
        approval_id=approval_id,
        proposal=proposal,
    )


def require_proposable(request: HiringRequest, proposal: HireModelProposal) -> None:
    """Refuse a hire no configured model can run.

    An agent is a fixed (role, model) unit, so a hire with no pair
    is not a hire. Refused before anything durable is written, because the
    alternative is what a live run produced: an approval an operator could see,
    could not approve (instantiation refuses an unbound request), and which
    nothing ever re-raised once a model became configurable. Enterable, no
    exit, nothing watching.

    Named by role rather than by request id: this message reaches an operator,
    and a database key tells them nothing about which hire failed or what to
    configure.

    Args:
        request: The hire being submitted.
        proposal: What the operator's live catalogue could offer it.

    Raises:
        HiringError: When nothing was proposable, carrying the catalogue's own
            reason so the operator knows what to change.
    """
    if proposal.recommended is not None:
        return
    reason = proposal.unmatched_reason or (
        "no configured provider offers a model this role can run"
    )
    msg = (
        f"Cannot open a hire for {request.role}: {reason}. An agent is a "
        "fixed (role, model) unit, so there is nothing to "
        "approve until a model this role can use is configured."
    )
    logger.warning(
        HR_HIRING_MODEL_UNSET,
        request_id=str(request.id),
        role=str(request.role),
        error=msg,
    )
    raise HiringError(msg)


async def submit_approval_item(
    store: ApprovalStoreProtocol,
    request: HiringRequest,
    candidate: CandidateCard,
    *,
    candidate_id: str,
    proposal: HireModelProposal,
) -> HiringRequest:
    """Create and store the approval item for a candidate.

    The recommendation is stamped onto the request so an approval taken without
    an explicit pick still has a binding to instantiate.

    Args:
        store: Where the item is written.
        request: The hiring request.
        candidate: The candidate to approve.
        candidate_id: ID of the candidate.
        proposal: The pairs this hire may be bound to.

    Returns:
        Updated request with approval metadata.
    """
    approval_id = str(uuid4())
    await store.add(
        build_approval(
            request,
            candidate,
            candidate_id=candidate_id,
            approval_id=approval_id,
            proposal=proposal,
        )
    )
    return request.model_copy(
        update={
            "selected_candidate_id": candidate_id,
            "approval_id": approval_id,
            "bound_model_ref": recommended_ref(proposal),
        },
    )


async def retire_unbacked_approval(
    store: ApprovalStoreProtocol | None, *, request: HiringRequest
) -> None:
    """Take back an approval item whose request never persisted.

    The inverse of :func:`submit_approval_item`, and the reason it exists is
    that the item lands in the store before the request that explains it is
    written. Left behind, it parks a decision naming a hire no surviving row
    describes.

    Args:
        store: Where the item was written, or ``None`` when there is none.
        request: The approval-stamped copy that failed to persist.
    """
    request_id = str(request.id)
    approval_id = request.approval_id
    if store is None or approval_id is None:
        return
    try:
        await store.delete(NotBlankStr(approval_id))
    except Exception as exc:  # noqa: BLE001 -- compensation must not mask
        reraise_critical(exc)
        # Logged rather than raised: the caller is already unwinding a failure,
        # and replacing it with this one would report the compensation instead
        # of the thing that actually went wrong.
        logger.warning(
            HR_HIRING_APPROVAL_ORPHANED,
            request_id=request_id,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = [
    "build_approval",
    "propose_models",
    "recommended_ref",
    "require_proposable",
    "retire_unbacked_approval",
    "submit_approval_item",
]
