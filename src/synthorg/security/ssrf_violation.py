"""SSRF violation model for self-healing security settings.

Records SSRF-blocked URLs during provider discovery, enabling
operators to review and allow/deny blocked hosts via the dashboard.
"""

from enum import StrEnum
from typing import Self
from urllib.parse import urlparse

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.core.types import NotBlankStr


class SsrfViolationStatus(StrEnum):
    """Status of an SSRF violation record."""

    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"


class SsrfViolation(BaseModel):
    """Record of an SSRF-blocked URL during provider discovery.

    Attributes:
        id: Unique violation identifier.
        timestamp: When the violation occurred.
        url: The blocked URL (redacted, no credentials).
        hostname: Extracted hostname from the URL.
        port: Port number.
        resolved_ip: IP address the hostname resolved to (if available).
        blocked_range: The CIDR range that caused the block (if available).
        provider_name: Provider preset name (if known).
        status: Current status (pending, allowed, denied).
        resolved_by: User who resolved the violation.
        resolved_at: When the violation was resolved.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    timestamp: AwareDatetime
    url: NotBlankStr

    @field_validator("url")
    @classmethod
    def _reject_unredacted_url(cls, v: str) -> str:
        """Reject URLs that contain credentials in userinfo."""
        parsed = urlparse(v)
        if parsed.username or parsed.password:
            msg = "url must be redacted -- credentials detected in userinfo"
            raise ValueError(msg)
        return v

    hostname: NotBlankStr
    port: int = Field(ge=1, le=65535)
    resolved_ip: NotBlankStr | None = None
    blocked_range: NotBlankStr | None = None
    provider_name: NotBlankStr | None = None
    status: SsrfViolationStatus = SsrfViolationStatus.PENDING
    resolved_by: NotBlankStr | None = None
    resolved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_resolution(self) -> Self:
        """Enforce that resolved_by/resolved_at are set for non-pending."""
        if self.status == SsrfViolationStatus.PENDING:
            if self.resolved_by is not None or self.resolved_at is not None:
                msg = "resolved_by and resolved_at must be None for pending violations"
                raise ValueError(msg)
        elif self.resolved_by is None or self.resolved_at is None:
            msg = "resolved_by and resolved_at are required for non-pending violations"
            raise ValueError(msg)
        return self
