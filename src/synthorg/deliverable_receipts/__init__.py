"""Deliverable receipts (provenance bundles).

Attaches an immutable, self-validating provenance receipt to every
completed deliverable: sources used, key decisions and rationale, cost,
tests run, red-team findings, and the replayable cassette reference. The
receipt is persisted as the system of record, projected into the
deliverable's living document, surfaced over REST, and validated for
consistency under the simulation harness.
"""

from synthorg.deliverable_receipts.errors import (
    DeliverableReceiptBuildError,
    DeliverableReceiptError,
    DeliverableReceiptNotFoundError,
)
from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptCassetteRef,
    ReceiptDecisionEntry,
    ReceiptRedTeamEntry,
    ReceiptSourceEntry,
    ReceiptTestEntry,
)

__all__ = [
    "DeliverableReceipt",
    "DeliverableReceiptBuildError",
    "DeliverableReceiptError",
    "DeliverableReceiptNotFoundError",
    "ReceiptCassetteRef",
    "ReceiptDecisionEntry",
    "ReceiptRedTeamEntry",
    "ReceiptSourceEntry",
    "ReceiptTestEntry",
]
