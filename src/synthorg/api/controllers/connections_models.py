# module-kind: code
"""Request DTOs for the connections controller.

Extracted from ``connections.py`` so the controller module stays within
its size budget. Both models forbid unknown keys at the boundary and cap
string lengths on attacker-controllable fields.
"""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import AuthMethod, ConnectionType

_MAX_NAME_LEN: Final[int] = 128
_MAX_BASE_URL_LEN: Final[int] = 2048
_MAX_CRED_VALUE_LEN: Final[int] = 8192
_MAX_METADATA_KEY_LEN: Final[int] = 128
_MAX_METADATA_VALUE_LEN: Final[int] = 1024


class CreateConnectionRequest(BaseModel):
    """Request body for ``POST /connections``.

    ``extra="forbid"`` rejects unknown keys at the boundary so the API
    never silently ACKs payloads it did not actually accept (typos,
    fabricated capability flags, stale client schemas). Field types
    enforce the same shape the controller previously checked inline,
    and ``max_length`` caps prevent unbounded string allocation on
    attacker-controllable input.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(max_length=_MAX_NAME_LEN)
    connection_type: ConnectionType
    auth_method: AuthMethod = AuthMethod.API_KEY
    credentials: dict[
        NotBlankStr,
        Annotated[str, Field(max_length=_MAX_CRED_VALUE_LEN)],
    ] = Field(
        default_factory=dict,
        description="Credential field-name to value map sent to the secret backend.",
    )
    base_url: Annotated[NotBlankStr, Field(max_length=_MAX_BASE_URL_LEN)] | None = None
    metadata: (
        dict[
            Annotated[NotBlankStr, Field(max_length=_MAX_METADATA_KEY_LEN)],
            Annotated[str, Field(max_length=_MAX_METADATA_VALUE_LEN)],
        ]
        | None
    ) = None
    health_check_enabled: bool = Field(
        default=True,
        description="Whether periodic health checks run against the connection.",
    )
    webhook_receipt_retention_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-connection webhook-receipt retention window in days; "
            "None uses the global default, 0 opts out of the sweep."
        ),
    )
    sensitive: bool = Field(
        default=False,
        description=(
            "Marks the connection sensitive so every external-access call "
            "against it routes to approval."
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        """Persist the canonical trimmed connection name.

        Returns:
            The name with surrounding whitespace stripped.
        """
        return v.strip()


class UpdateConnectionRequest(BaseModel):
    """Request body for ``PATCH /connections/{name}`` (partial update).

    Each field is optional; absent fields keep their stored value.
    ``extra="forbid"`` mirrors :class:`CreateConnectionRequest`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    base_url: Annotated[NotBlankStr, Field(max_length=_MAX_BASE_URL_LEN)] | None = None
    metadata: (
        dict[
            Annotated[NotBlankStr, Field(max_length=_MAX_METADATA_KEY_LEN)],
            Annotated[str, Field(max_length=_MAX_METADATA_VALUE_LEN)],
        ]
        | None
    ) = None
    health_check_enabled: bool | None = Field(
        default=None,
        description="Whether periodic health checks run against the connection.",
    )
    webhook_receipt_retention_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-connection webhook-receipt retention window in days; "
            "None uses the global default, 0 opts out of the sweep."
        ),
    )
    sensitive: bool | None = Field(
        default=None,
        description=(
            "Marks the connection sensitive so every external-access call "
            "against it routes to approval."
        ),
    )

    @field_validator("sensitive")
    @classmethod
    def _reject_null_sensitive(
        cls,
        v: bool | None,  # noqa: FBT001 -- Pydantic validator value is positional
    ) -> bool | None:
        """Reject an explicit JSON ``null`` for ``sensitive``.

        Returns:
            The validated value (omit / true / false).

        Raises:
            ValueError: When an explicit ``null`` is supplied.
        """
        if v is None:
            msg = "sensitive must be true or false, not null"
            raise ValueError(msg)
        return v
