"""Deliverable-receipts feature state slice.

Holds the receipt service, wired at boot once persistence is connected.
``None`` until wired; the receipt controller raises 503 on a ``None``
service.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.deliverable_receipts.service import DeliverableReceiptService

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class DeliverableReceiptStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the deliverable-receipts feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: DeliverableReceiptService | None = None


def deliverable_receipt_service_of(
    app_state: AppStateSliceMixin,
) -> DeliverableReceiptService:
    """Resolve the receipt service from its slice, or raise 503.

    Returns:
        The wired deliverable-receipt service.
    """
    return require_service(
        app_state.slice(DeliverableReceiptStateSlice).service,
        "Deliverable Receipt Service",
    )
