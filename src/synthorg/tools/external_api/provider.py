"""Pluggable egress provider for the governed external-access tool.

The tool owns governance (credential brokering, SSRF validation, rate
limiting, approval gating) and delegates the actual HTTP egress to an
:class:`ExternalAccessProvider`. The default :class:`httpx`-based strategy
makes DNS-pinned requests directly; a future strategy (e.g. a sidecar proxy)
can register under its own config discriminator without touching the tool.

Credentials live in ``ExternalAccessRequest.headers`` and MUST NOT be logged
by any provider implementation.
"""

from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type


class ExternalAccessRequest(BaseModel):
    """A fully-resolved, governance-cleared outbound request.

    Built by the tool after credential injection and SSRF validation. The
    ``pinned_ip`` / ``pinned_hostname`` pair, when both set, instructs the
    provider to pin DNS (close the rebinding TOCTOU window) while preserving
    the hostname for TLS SNI.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    method: NotBlankStr
    url: NotBlankStr
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    timeout_seconds: float = Field(gt=0)
    max_response_bytes: int = Field(gt=0)
    pinned_ip: str | None = None
    pinned_hostname: str | None = None

    @model_validator(mode="after")
    def _validate_pinning_pair(self) -> Self:
        """Pin both the IP and the hostname, or neither.

        A provider pins the validated IP at connect time while keeping the
        hostname for TLS SNI; an IP without its hostname (or vice versa)
        is a malformed pin that would break SNI / certificate validation.
        """
        if (self.pinned_ip is None) != (self.pinned_hostname is None):
            msg = "pinned_ip and pinned_hostname must be set together or both omitted"
            raise ValueError(msg)
        return self


class ExternalAccessResponse(BaseModel):
    """The upstream response, body already truncated to the byte budget."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str
    truncated: bool = False


@runtime_checkable
class ExternalAccessProvider(Protocol):
    """Executes a governance-cleared external request and returns the response.

    Implementations:
        * MUST NOT log request headers or body (they carry credentials).
        * SHOULD return every HTTP response (including 4xx/5xx) so the agent
          can react to API-level status; raise
          :class:`~synthorg.tools.external_api.errors.ExternalApiResponseError`
          only for transport-level failures (timeout, connection, protocol).
        * MUST honour ``follow_redirects=False`` semantics so a 3xx to an
          un-validated host cannot bypass the egress allowlist.
    """

    async def request(
        self,
        req: ExternalAccessRequest,
    ) -> ExternalAccessResponse:
        """Perform *req* and return the (truncated) response."""
        ...
