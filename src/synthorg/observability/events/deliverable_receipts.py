"""Structured-log event names for the deliverable-receipts feature.

Covers receipt assembly, validation, living-doc rendering, the two
capture sinks (knowledge usage, test runs), and the persistence-layer
failure events for the three feature-owned tables. Those failure events
use this feature's ``deliverable_receipts.*`` namespace, so they live in
this domain module rather than the cross-cutting ``persistence.*`` namespace.
"""

from typing import Final

# -- Receipt lifecycle ----------------------------------------------------

RECEIPT_BUILT: Final[str] = "deliverable_receipts.receipt.built"
RECEIPT_BUILD_FAILED: Final[str] = "deliverable_receipts.receipt.build_failed"
RECEIPT_BUILD_SKIPPED: Final[str] = "deliverable_receipts.receipt.build_skipped"
RECEIPT_RENDERED: Final[str] = "deliverable_receipts.receipt.rendered"
RECEIPT_VALIDATED: Final[str] = "deliverable_receipts.receipt.validated"
RECEIPT_VALIDATION_FAILED: Final[str] = "deliverable_receipts.receipt.validation_failed"
RECEIPT_REDTEAM_UNAVAILABLE: Final[str] = (
    "deliverable_receipts.receipt.redteam_unavailable"
)
RECEIPT_CASSETTE_UNAVAILABLE: Final[str] = (
    "deliverable_receipts.receipt.cassette_unavailable"
)
RECEIPT_MIXED_CURRENCY_COST: Final[str] = (
    "deliverable_receipts.receipt.mixed_currency_cost"
)

# -- Capture sinks --------------------------------------------------------

KNOWLEDGE_USAGE_RECORDED: Final[str] = "deliverable_receipts.knowledge_usage.recorded"
KNOWLEDGE_USAGE_RECORD_FAILED: Final[str] = (
    "deliverable_receipts.knowledge_usage.record_failed"
)
TEST_RUN_RECORDED: Final[str] = "deliverable_receipts.test_run.recorded"
TEST_RUN_RECORD_FAILED: Final[str] = "deliverable_receipts.test_run.record_failed"

# -- Persistence: deliverable_receipt -------------------------------------

PERSISTENCE_RECEIPT_SAVE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.receipt_save_failed"
)
PERSISTENCE_RECEIPT_QUERY_FAILED: Final[str] = (
    "deliverable_receipts.persistence.receipt_query_failed"
)
PERSISTENCE_RECEIPT_DELETE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.receipt_delete_failed"
)
PERSISTENCE_RECEIPT_DESERIALIZE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.receipt_deserialize_failed"
)

# -- Persistence: knowledge_usage_record ----------------------------------

PERSISTENCE_KNOWLEDGE_USAGE_SAVE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.knowledge_usage_save_failed"
)
PERSISTENCE_KNOWLEDGE_USAGE_QUERY_FAILED: Final[str] = (
    "deliverable_receipts.persistence.knowledge_usage_query_failed"
)
PERSISTENCE_KNOWLEDGE_USAGE_DELETE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.knowledge_usage_delete_failed"
)
PERSISTENCE_KNOWLEDGE_USAGE_DESERIALIZE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.knowledge_usage_deserialize_failed"
)

# -- Persistence: code_execution_record -----------------------------------

PERSISTENCE_CODE_EXECUTION_SAVE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.code_execution_save_failed"
)
PERSISTENCE_CODE_EXECUTION_QUERY_FAILED: Final[str] = (
    "deliverable_receipts.persistence.code_execution_query_failed"
)
PERSISTENCE_CODE_EXECUTION_DELETE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.code_execution_delete_failed"
)
PERSISTENCE_CODE_EXECUTION_DESERIALIZE_FAILED: Final[str] = (
    "deliverable_receipts.persistence.code_execution_deserialize_failed"
)
