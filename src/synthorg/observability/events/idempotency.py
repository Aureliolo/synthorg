"""Idempotency-key lifecycle event constants."""

from typing import Final

IDEMPOTENCY_CLAIM_FRESH: Final[str] = "idempotency.claim.fresh"
IDEMPOTENCY_CLAIM_IN_FLIGHT: Final[str] = "idempotency.claim.in_flight"
IDEMPOTENCY_CLAIM_COMPLETED: Final[str] = "idempotency.claim.completed"
IDEMPOTENCY_CLAIM_FAILED_REPLAY: Final[str] = "idempotency.claim.failed_replay"
IDEMPOTENCY_COMPLETE: Final[str] = "idempotency.complete"
IDEMPOTENCY_FAIL: Final[str] = "idempotency.fail"
IDEMPOTENCY_CLEANUP: Final[str] = "idempotency.cleanup"
IDEMPOTENCY_PERSISTENCE_ERROR: Final[str] = "idempotency.persistence_error"
