# module-kind: code
"""Checking that a brief which stands up an initiative was authorised.

The claim and its truth are separate questions with separate homes. The claim
is structural: :class:`~synthorg.engine.pipeline.models.WorkItem` refuses to
be built with ``plan_required`` and no ``charter_id``, so no adapter can open
an initiative without naming an approval. The truth is asked here, where the
charter store is reachable, on every plan-forcing brief.
"""

from synthorg.engine.pipeline.charter_authority_port import (
    CharterAuthorisation,
    CharterAuthority,
)
from synthorg.engine.pipeline.errors import WorkInitiativeUnauthorisedError
from synthorg.engine.pipeline.models import WorkItem
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import PIPELINE_INITIATIVE_UNAUTHORISED

logger = get_logger(__name__)


async def require_authorised_initiative(
    work_item: WorkItem,
    authority: CharterAuthority | None,
) -> None:
    """Refuse a brief that stands up an initiative nobody approved.

    A brief that forces no plan commits the organisation to nothing beyond
    one task, so it is not asked about.

    Args:
        work_item: The brief entering the spine.
        authority: The attached charter store, or ``None``.

    Raises:
        WorkInitiativeUnauthorisedError: When no charter store is attached,
            when the named charter does not exist, or when it exists and no
            operator approved it.
    """
    if not work_item.plan_required:
        return
    charter_id = work_item.charter_id
    if authority is None or charter_id is None:
        # ``charter_id is None`` is unreachable through the validator and
        # checked anyway: it is the difference between refusing and crashing
        # if the two invariants ever drift apart.
        msg = (
            "no charter store is attached, so the approval this brief "
            "claims cannot be verified"
        )
        raise WorkInitiativeUnauthorisedError(msg)
    verdict = await authority.authorisation_of(charter_id)
    if verdict is CharterAuthorisation.APPROVED:
        return
    logger.warning(
        PIPELINE_INITIATIVE_UNAUTHORISED,
        correlation_id=work_item.correlation_id,
        charter_id=charter_id,
        verdict=verdict.value,
    )
    detail = (
        "names a charter that does not exist"
        if verdict is CharterAuthorisation.UNKNOWN
        else "names a charter no operator approved"
    )
    msg = f"This brief {detail}, so it cannot stand up an initiative"
    raise WorkInitiativeUnauthorisedError(msg)


__all__ = ["require_authorised_initiative"]
