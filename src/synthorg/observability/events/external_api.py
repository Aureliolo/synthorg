"""Event constants for the governed external-access tool."""

from typing import Final

EXTERNAL_API_CALL_STARTED: Final[str] = "external_api.call.started"
EXTERNAL_API_CALL_SUCCEEDED: Final[str] = "external_api.call.succeeded"
EXTERNAL_API_CALL_FAILED: Final[str] = "external_api.call.failed"
EXTERNAL_API_CONNECTION_NOT_FOUND: Final[str] = "external_api.connection.not_found"
EXTERNAL_API_APPROVAL_REQUIRED: Final[str] = "external_api.approval.required"
EXTERNAL_API_APPROVAL_CONSUMED: Final[str] = "external_api.approval.consumed"
EXTERNAL_API_SIGNATURE_MISMATCH: Final[str] = "external_api.signature.mismatch"
EXTERNAL_API_EGRESS_BLOCKED: Final[str] = "external_api.egress.blocked"
EXTERNAL_API_RATE_LIMITED: Final[str] = "external_api.rate_limited"
EXTERNAL_API_RISK_CLASSIFY_FAILED: Final[str] = "external_api.risk_classify.failed"
