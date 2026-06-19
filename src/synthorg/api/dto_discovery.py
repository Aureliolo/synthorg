"""Discovery allowlist DTOs for the provider management API."""

from typing import Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

from synthorg.core.types import NotBlankStr

_HOST_PORT_PATTERN = r"^[a-zA-Z0-9._\[\]%-]+:[0-9]{1,5}$"

_MAX_PORT: Final[int] = 65535


class DiscoveryPolicyResponse(BaseModel):
    """Current state of the provider discovery SSRF allowlist.

    Attributes:
        host_port_allowlist: Trusted host:port pairs.
        block_private_ips: Whether private IP blocking is active.
        entry_count: Number of entries in the allowlist (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    host_port_allowlist: tuple[NotBlankStr, ...] = ()
    block_private_ips: bool = True

    @computed_field
    @property
    def entry_count(self) -> int:
        """Number of entries in the allowlist.

        Returns:
            Resulting integer.
        """
        return len(self.host_port_allowlist)


def _validate_host_port(v: str) -> str:
    """Validate that the port portion of a host:port string is in range.

    Args:
        v: Validated host:port string (already passed regex).

    Returns:
        The original string if valid.

    Raises:
        ValueError: If the port is outside 1-65535.
    """
    port_str = v.rsplit(":", 1)[-1]
    port = int(port_str)
    if not 1 <= port <= _MAX_PORT:
        msg = f"Port must be in range 1-{_MAX_PORT}, got {port}"
        raise ValueError(msg)
    return v


class AddAllowlistEntryRequest(BaseModel):
    """Payload for adding a host:port entry to the discovery allowlist.

    Attributes:
        host_port: Entry to add (e.g. ``"my-server:8080"``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    host_port: NotBlankStr = Field(
        max_length=256,
        pattern=_HOST_PORT_PATTERN,
    )

    @field_validator("host_port")
    @classmethod
    def _check_port_range(cls, v: str) -> str:
        """Check port range.

        Returns:
            Resulting string.
        """
        return _validate_host_port(v)


class RemoveAllowlistEntryRequest(BaseModel):
    """Payload for removing a host:port entry from the discovery allowlist.

    Attributes:
        host_port: Entry to remove.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    host_port: NotBlankStr = Field(
        max_length=256,
        pattern=_HOST_PORT_PATTERN,
    )

    @field_validator("host_port")
    @classmethod
    def _check_port_range(cls, v: str) -> str:
        """Check port range.

        Returns:
            Resulting string.
        """
        return _validate_host_port(v)
