"""Helpers for the ``GET /meta/proposals`` listing endpoint."""

from typing import Final

from synthorg.core.approval import ApprovalItem
from synthorg.meta.guards.approval_gate import PROPOSAL_GUARD_ACTION_TYPE_PREFIX
from synthorg.meta.models import ProposalAltitude
from synthorg.meta.signals.service import PROPOSAL_ACTION_TYPE

PROPOSAL_ACTION_TYPES: Final[tuple[str, ...]] = (
    PROPOSAL_ACTION_TYPE,
    *(
        f"{PROPOSAL_GUARD_ACTION_TYPE_PREFIX}{altitude.value}"
        for altitude in ProposalAltitude
    ),
)
"""Every action-type value ``GET /meta/proposals`` matches.

``ProposalAltitude`` is a closed enum, so the automated guard's
altitude-suffixed action types (``PROPOSAL_GUARD_ACTION_TYPE_PREFIX``)
form a fixed, enumerable set rather than an open-ended prefix -- this
lets the repo query push the filter down as a plain ``IN`` clause.
"""


def proposal_to_dict(item: ApprovalItem) -> dict[str, object]:
    """Serialise an approval item for the proposals listing endpoint.

    Returns:
        A JSON-serialisable proposal dict.
    """
    return {
        "id": item.id,
        "title": item.title,
        "action_type": item.action_type,
        "status": item.status.value,
        "risk_level": item.risk_level.value,
        "requested_by": item.requested_by,
        "created_at": item.created_at.isoformat(),
    }


__all__ = ["PROPOSAL_ACTION_TYPES", "proposal_to_dict"]
