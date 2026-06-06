# module-kind: declarative
"""Persistence event constants for the webhook_receipt sub-domain (provider log)."""

from typing import Final

PERSISTENCE_WEBHOOK_RECEIPT_LOGGED: Final[str] = "persistence.webhook_receipt.logged"
PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED: Final[str] = (
    "persistence.webhook_receipt.log_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_LISTED: Final[str] = "persistence.webhook_receipt.listed"
PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED: Final[str] = (
    "persistence.webhook_receipt.list_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_DELETE_FAILED: Final[str] = (
    "persistence.webhook_receipt.delete_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP: Final[str] = "persistence.webhook_receipt.cleanup"
PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED: Final[str] = (
    "persistence.webhook_receipt.cleanup_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_PAUSED: Final[str] = (
    "persistence.webhook_receipt.cleanup_paused"
)
PERSISTENCE_WEBHOOK_RECEIPT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.webhook_receipt.deserialize_failed"
)
