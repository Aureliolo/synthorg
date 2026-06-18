"""Wire DTOs for the SSRF-violation review surface."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus


class SsrfViolationDTO(BaseModel):
    """A recorded SSRF-blocked outbound URL on the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: NotBlankStr = Field(description="Unique violation identifier.")
    timestamp: datetime = Field(description="When the outbound request was blocked.")
    url: NotBlankStr = Field(description="The blocked URL (credentials redacted).")
    hostname: NotBlankStr = Field(description="Hostname extracted from the URL.")
    port: int = Field(ge=1, le=65535, description="Destination port.")
    resolved_ip: NotBlankStr | None = Field(
        default=None,
        description="IP the hostname resolved to, when known.",
    )
    blocked_range: NotBlankStr | None = Field(
        default=None,
        description="CIDR range that triggered the block, when known.",
    )
    provider_name: NotBlankStr | None = Field(
        default=None,
        description="Provider preset that triggered the block, when known.",
    )
    status: SsrfViolationStatus = Field(
        description="Review status: pending, allowed, or denied.",
    )
    resolved_by: NotBlankStr | None = Field(
        default=None,
        description="Operator who resolved the violation, when resolved.",
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="When the violation was resolved, when resolved.",
    )

    @classmethod
    def from_entity(cls, entity: SsrfViolation) -> SsrfViolationDTO:
        """Project a stored violation onto the wire DTO.

        Returns:
            The wire representation.
        """
        return cls(
            id=NotBlankStr(str(entity.id)),
            timestamp=entity.timestamp,
            url=entity.url,
            hostname=entity.hostname,
            port=entity.port,
            resolved_ip=entity.resolved_ip,
            blocked_range=entity.blocked_range,
            provider_name=entity.provider_name,
            status=entity.status,
            resolved_by=entity.resolved_by,
            resolved_at=entity.resolved_at,
        )


class ResolveSsrfViolationRequest(BaseModel):
    """Operator decision to allow or deny a pending SSRF violation."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: SsrfViolationStatus = Field(
        description="Resolution decision; must be 'allowed' or 'denied'.",
    )
