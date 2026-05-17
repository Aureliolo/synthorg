"""Configuration model for web tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.tools.network_validator import NetworkPolicy


class WebToolsConfig(BaseModel):
    """Configuration for web tool category.

    Attributes:
        network_policy: Network policy for SSRF prevention.
        max_response_bytes: Maximum response body size to return.
        request_timeout: Default HTTP request timeout in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    network_policy: NetworkPolicy = Field(
        default_factory=NetworkPolicy,
        description="Network policy for SSRF prevention",
    )
    max_response_bytes: int = Field(
        default=1_048_576,
        gt=0,
        description="Maximum response body size (bytes)",
    )
    request_timeout: float = Field(
        default=30.0,
        gt=0,
        le=300.0,
        description="Default HTTP request timeout (seconds)",
    )
