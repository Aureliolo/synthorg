# module-kind: feature
"""Deliverable-receipts feature manifest.

Declares the feature's state slice, REST controller, and the
boot-constructed symbols the ghost-wiring gate tracks. The feature has
no settings namespace (it is gated on a connected persistence backend
and a wired docs service, not operator settings).
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.deliverable_receipts.api_controller import DeliverableReceiptController
from synthorg.deliverable_receipts.state_slice import DeliverableReceiptStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="deliverable_receipts",
    settings_namespace=None,
    state_slice=DeliverableReceiptStateSlice,
    controllers=(DeliverableReceiptController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "build_deliverable_receipt_service",
        "DeliverableReceiptService",
        "ReceiptBuilder",
        "ReceiptValidator",
        "ReceiptRenderer",
    ),
    depends_on=(),
)
