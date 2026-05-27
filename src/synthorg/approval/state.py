"""Approval feature state slice.

Holds the approval store (always wired), the approval gate, the
approval-timeout scheduler, and the review-gate service. The gate /
scheduler / review-gate are wired after persistence connects and are
``None`` until then; readers guard on their absence.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.approval.protocol import ApprovalStoreProtocol  # noqa: TC001
from synthorg.engine.approval_gate import ApprovalGate  # noqa: TC001
from synthorg.engine.review_gate import ReviewGateService  # noqa: TC001
from synthorg.security.timeout.scheduler import (
    ApprovalTimeoutScheduler,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class ApprovalStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the approval feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    store: ApprovalStoreProtocol | None = None
    gate: ApprovalGate | None = None
    timeout_scheduler: ApprovalTimeoutScheduler | None = None
    review_gate: ReviewGateService | None = None


def approval_store_of(app_state: AppStateSliceMixin) -> ApprovalStoreProtocol:
    """Resolve the approval store from its slice, or raise 503.

    Returns:
        The wired approval store.
    """
    return require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")
