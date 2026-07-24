# module-kind: code
"""Request DTOs for the connections controller.

Extracted from ``connections.py`` so the controller module stays within
its size budget. Both models forbid unknown keys at the boundary and cap
string lengths on attacker-controllable fields.
"""

from typing import Annotated, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.field_metadata import reject_inline_secret_fields
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.connections.repo_scope import validate_repo_scope_entry

_MAX_NAME_LEN: Final[int] = 128
_MAX_BASE_URL_LEN: Final[int] = 2048
_MAX_CRED_VALUE_LEN: Final[int] = 8192
_MAX_METADATA_KEY_LEN: Final[int] = 128
_MAX_METADATA_VALUE_LEN: Final[int] = 1024
_MAX_REPO_ENTRIES: Final[int] = 1000
_MAX_REPO_ENTRY_LEN: Final[int] = 512

_AllowedRepos = tuple[
    Annotated[NotBlankStr, Field(max_length=_MAX_REPO_ENTRY_LEN)], ...
]


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
        description=(
            "Non-secret credential field-name to value map. Secret fields "
            "(tokens/passwords/keys) are NOT sent here: capture them out of "
            "band and pass their handles via ``credential_handles``."
        ),
    )
    credential_handles: dict[
        NotBlankStr,
        Annotated[NotBlankStr, Field(max_length=_MAX_NAME_LEN)],
    ] = Field(
        default_factory=dict,
        description=(
            "Secret credential field-name to opaque capture-handle map. Each "
            "handle is resolved once, in-process, against its "
            "``(connection_draft_id, field)`` binding so the raw value never "
            "enters the request body or the logs. Requires connection_draft_id."
        ),
    )
    connection_draft_id: (
        Annotated[NotBlankStr, Field(max_length=_MAX_NAME_LEN)] | None
    ) = Field(
        default=None,
        description=(
            "Client-generated draft id the secret-capture handles are bound "
            "to; required when credential_handles are supplied."
        ),
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
    allowed_repos: Annotated[_AllowedRepos, Field(max_length=_MAX_REPO_ENTRIES)] = (
        Field(
            default=(),
            description=(
                "Least-privilege forge repository scope ('owner/repo', 'owner/*' "
                "globs). Empty denies every repository (fail-closed)."
            ),
        )
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        """Persist the canonical trimmed connection name.

        Returns:
            The name with surrounding whitespace stripped.
        """
        return v.strip()

    @field_validator("allowed_repos")
    @classmethod
    def _validate_allowed_repos(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        """Reject an over-broad or malformed repo-scope entry at the boundary.

        Returns:
            The validated scope tuple.
        """
        for entry in v:
            validate_repo_scope_entry(str(entry))
        return v

    @model_validator(mode="after")
    def _validate_credentials(self) -> Self:
        """Enforce the credential-boundary invariants at request parse time.

        Credential handles must carry a ``connection_draft_id`` to bind
        against, and a secret field must be captured out of band (never sent
        inline in ``credentials``). Enforcing both here makes them structurally
        unbypassable and identical to the ``connections.create`` MCP args.

        Returns:
            ``self`` when both invariants hold.

        Raises:
            ValueError: If handles lack a draft id, or a secret field is inline.
        """
        if self.credential_handles and self.connection_draft_id is None:
            msg = "connection_draft_id is required when credential_handles are supplied"
            raise ValueError(msg)
        reject_inline_secret_fields(self.connection_type, self.credentials.keys())
        return self


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
    allowed_repos: (
        Annotated[_AllowedRepos, Field(max_length=_MAX_REPO_ENTRIES)] | None
    ) = Field(
        default=None,
        description=(
            "Replace the forge repository scope ('owner/repo', 'owner/*' "
            "globs). Send [] to clear it (deny-all); omit to keep the "
            "existing scope."
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

    @field_validator("allowed_repos")
    @classmethod
    def _validate_allowed_repos(
        cls, v: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        """Reject an over-broad or malformed repo-scope entry at the boundary.

        Returns:
            The validated scope tuple (or ``None`` to keep the stored scope).
        """
        for entry in v or ():
            validate_repo_scope_entry(str(entry))
        return v


class RevealedSecretResponse(BaseModel):
    """Success payload for ``GET /connections/{name}/secrets/{field}``.

    A named DTO (replacing a bare ``dict[str, str]``) so the single
    revealed credential field is documented in the OpenAPI schema. The
    value is logged by field name only, never echoed to logs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    field: NotBlankStr = Field(description="Name of the revealed credential field.")
    # Deliberately a bare ``str`` (not ``NotBlankStr``): a credential value
    # can legitimately be empty (e.g. an optional, unset field), so blank is
    # a valid revealed value rather than a validation error.
    value: str = Field(description="Plaintext value of the credential field.")


class SecretCaptureRequest(BaseModel):
    """Request body for the out-of-band secret-capture endpoint.

    The raw ``value`` is typed :class:`SecretStr` so it renders masked in
    any accidental log/repr and never appears in a traceback dump; the
    endpoint hands it straight to the secret backend and returns only an
    opaque handle. ``secret_kind`` and ``conversation_id`` are metadata
    (recorded for binding/audit), never the value.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    value: SecretStr = Field(
        description="Raw secret value; written to the backend, never logged.",
    )
    secret_kind: NotBlankStr = Field(
        max_length=_MAX_NAME_LEN,
        description="Field kind this secret is for (e.g. token, password).",
    )
    conversation_id: Annotated[NotBlankStr, Field(max_length=_MAX_NAME_LEN)] | None = (
        Field(default=None, description="Owning conversation id (audit only).")
    )

    @field_validator("value")
    @classmethod
    def _bounded_secret_value(cls, v: SecretStr) -> SecretStr:
        """Bound the secret length without ever surfacing the raw value.

        A ``Field(max_length=...)`` on a ``SecretStr`` validates the inner
        string *before* masking, so an over-length value echoes raw plaintext
        in the ``ValidationError``. Checking the length here keeps the value
        masked: the error message carries only the limit, never the value.

        Returns:
            The validated secret.

        Raises:
            ValueError: If the value exceeds the maximum length.
        """
        if len(v.get_secret_value()) > _MAX_CRED_VALUE_LEN:
            msg = f"secret value exceeds the {_MAX_CRED_VALUE_LEN}-character limit"
            raise ValueError(msg)
        return v


class SecretCaptureResponse(BaseModel):
    """Success payload for the secret-capture endpoint: the opaque handle."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    handle: NotBlankStr = Field(
        description="Opaque single-use handle to pass as a credential handle.",
    )
