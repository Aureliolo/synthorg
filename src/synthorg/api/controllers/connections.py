"""Connections API controller.

CRUD endpoints for the external service connection catalog,
including on-demand health checks.
"""

import copy
from typing import Annotated

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import (  # noqa: TC001 -- runtime annotation
    PathField,
    PathName,
)
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.types import (
    NotBlankStr,  # noqa: TC001 -- Pydantic field type at runtime
)
from synthorg.integrations.connections.catalog import _UNSET, _UnsetType
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
    HealthReport,
)
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    DuplicateConnectionError,
    InvalidConnectionAuthError,
    SecretRetrievalError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_CONNECTION_CREATED,
    SECURITY_CONNECTION_DELETED,
    SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
    SECURITY_CONNECTION_SECRET_REVEALED,
    SECURITY_CONNECTION_UPDATED,
)

# Unified error surfaced to clients on any reveal failure. The
# message is deliberately opaque so callers cannot distinguish
# "connection missing" from "field missing" from "secret backend
# unavailable" -- all three would otherwise leak side-channel
# information about what connections exist and which fields are set.
_REVEAL_GENERIC_ERROR = "Connection or credential field not found"

logger = get_logger(__name__)


_MAX_BASE_URL_LEN = 2048
_MAX_CRED_VALUE_LEN = 8192


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

    name: NotBlankStr = Field(max_length=128)
    connection_type: ConnectionType
    auth_method: AuthMethod = AuthMethod.API_KEY
    # ``dict[NotBlankStr, ...]`` matches the catalog signature
    # (``catalog.create(..., credentials: dict[str, str], metadata:
    # dict[str, str])``) and the secret-backend reveal contract;
    # accepting non-string values would let a ``credentials["k"] = 42``
    # entry slip through and trigger ``SecretRetrievalError`` only at
    # reveal time. The key type is ``NotBlankStr`` so blank or
    # whitespace-only credential field names are rejected at the
    # boundary rather than landing in the secret backend with a key
    # that no reveal call can ever match. ``max_length`` on the value
    # type bounds payload size.
    credentials: dict[
        NotBlankStr,
        Annotated[str, Field(max_length=_MAX_CRED_VALUE_LEN)],
    ] = Field(default_factory=dict)
    base_url: Annotated[NotBlankStr, Field(max_length=_MAX_BASE_URL_LEN)] | None = None
    metadata: dict[NotBlankStr, str] | None = None
    health_check_enabled: bool = True
    # Per-connection override for the webhook-receipt cleanup window.
    # ``None`` falls back to the global ``integrations.webhook_receipt_retention_days``
    # setting; ``0`` opts this connection out of the sweep entirely.
    webhook_receipt_retention_days: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        # Persist the canonical trimmed form so ``"  github  "`` and
        # ``"github"`` cannot become two distinct identities.  The
        # ``NotBlankStr`` annotation already rejects whitespace-only
        # input; this just normalises the surrounding spaces.
        return v.strip()


class UpdateConnectionRequest(BaseModel):
    """Request body for ``PATCH /connections/{name}`` (partial update).

    Each field is optional; absent fields keep their stored value.
    ``extra="forbid"`` mirrors :class:`CreateConnectionRequest`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    base_url: Annotated[NotBlankStr, Field(max_length=_MAX_BASE_URL_LEN)] | None = None
    # Mirrors ``CreateConnectionRequest.metadata``: non-string values
    # are rejected at parse time rather than producing surprises later
    # in the catalog / health-check path, and ``NotBlankStr`` keys
    # block blank/whitespace-only metadata field names from reaching
    # the catalog.
    metadata: dict[NotBlankStr, str] | None = None
    health_check_enabled: bool | None = None
    # Same tri-state semantics as ``CreateConnectionRequest``:
    # ``None`` clears the override (falls back to global default),
    # an int sets the override, omitting the field keeps the existing
    # stored value (handled via ``model_fields_set`` below).
    webhook_receipt_retention_days: int | None = Field(default=None, ge=0)


class ConnectionsController(Controller):
    """CRUD and health endpoints for external connections."""

    path = "/connections"
    tags = ["Integrations"]  # noqa: RUF012

    @get(
        "/",
        guards=[require_read_access],
        summary="List all connections",
    )
    async def list_connections(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[Connection]:
        """List all connections in the catalog (paginated).

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated connection catalog entries.
        """
        app_state = state["app_state"]
        catalog = app_state.connection_catalog
        connections = await catalog.list_all()
        page, meta = paginate_cursor(
            tuple(connections),
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse[Connection](data=page, pagination=meta)

    @get(
        "/{name:str}",
        guards=[require_read_access],
        summary="Get a connection by name",
    )
    async def get_connection(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[Connection]:
        """Get a single connection by name."""
        catalog = state["app_state"].connection_catalog
        conn = require_resource_or_404(
            await catalog.get(name),
            resource_type="Connection",
            identifier=name,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"connection": name},
            code=ErrorCode.CONNECTION_NOT_FOUND,
        )
        return ApiResponse(data=conn)

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("connections.create", key="user"),
        ],
        summary="Create a connection",
    )
    async def create_connection(
        self,
        state: State,
        data: CreateConnectionRequest,
    ) -> ApiResponse[Connection]:
        """Create a new connection.

        Litestar runs Pydantic validation on
        :class:`CreateConnectionRequest` before this handler executes;
        unknown fields and shape mismatches surface as a structured 4xx
        response automatically.  The handler body therefore only owns
        the persistence-layer dispatch and the domain-error mapping.
        """
        # Defensively deepcopy ``credentials`` / ``metadata`` at the API
        # boundary so the catalog can never observe (or be mutated by)
        # subsequent caller-owned changes to the request payload.  The
        # secret backend persists ``credentials`` in plaintext briefly
        # before encryption, so a shared-reference write would be
        # particularly dangerous here.
        credentials_copy = copy.deepcopy(data.credentials)
        metadata_copy = (
            copy.deepcopy(data.metadata) if data.metadata is not None else None
        )
        catalog = state["app_state"].connection_catalog
        try:
            conn = await catalog.create(
                name=data.name,
                connection_type=data.connection_type,
                auth_method=data.auth_method.value,
                credentials=credentials_copy,
                base_url=data.base_url,
                metadata=metadata_copy,
                health_check_enabled=data.health_check_enabled,
                webhook_receipt_retention_days=data.webhook_receipt_retention_days,
            )
        except DuplicateConnectionError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                connection=data.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        except InvalidConnectionAuthError as exc:
            # ``InvalidConnectionAuthError`` is raised by
            # ``authenticator.validate_credentials(credentials)``, so
            # the offending field is ``credentials`` rather than
            # ``auth_method``; mislabelling here would point clients at
            # the wrong input.
            logger.warning(
                API_VALIDATION_FAILED,
                connection=data.name,
                field="credentials",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(str(exc)) from exc
        # Connection records carry credentials; route the success
        # event through the audit chain (the SECURITY_* prefix is the
        # ``AuditChainSink`` filter). Field naming mirrors
        # ``SECURITY_PROVIDER_CREATED`` (bare resource name) so
        # forensic queries use a single key across the security event
        # surface.
        logger.info(
            SECURITY_CONNECTION_CREATED,
            connection=data.name,
            connection_type=data.connection_type.value,
            auth_method=data.auth_method.value,
        )
        return ApiResponse(data=conn)

    @patch(
        "/{name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("connections.update", key="user"),
        ],
        summary="Update a connection",
    )
    async def update_connection(
        self,
        state: State,
        name: PathName,
        data: UpdateConnectionRequest,
    ) -> ApiResponse[Connection]:
        """Update mutable fields of a connection.

        Litestar runs Pydantic validation on
        :class:`UpdateConnectionRequest` before this handler executes;
        unknown fields and shape mismatches surface as a structured 4xx
        response automatically.
        """
        # ``model_fields_set`` distinguishes "field omitted" from "field
        # explicitly set to ``None``" so a PATCH that drops ``base_url``
        # can still null out the stored value via ``base_url=None``;
        # when the field was omitted we forward ``_UNSET`` to keep the
        # catalog's existing value.  All four mutable fields use the
        # same semantic so client behaviour is uniform.
        base_url: str | None | _UnsetType = (
            data.base_url if "base_url" in data.model_fields_set else _UNSET
        )
        metadata: dict[str, str] | None | _UnsetType
        if "metadata" in data.model_fields_set:
            # Defensively deepcopy when provided; same reasoning as
            # ``create_connection`` (catalog briefly holds the mapping
            # before persisting / encrypting nested secrets).
            metadata = (
                copy.deepcopy(data.metadata) if data.metadata is not None else None
            )
        else:
            metadata = _UNSET
        health_check_enabled: bool | None | _UnsetType = (
            data.health_check_enabled
            if "health_check_enabled" in data.model_fields_set
            else _UNSET
        )
        webhook_receipt_retention_days: int | None | _UnsetType = (
            data.webhook_receipt_retention_days
            if "webhook_receipt_retention_days" in data.model_fields_set
            else _UNSET
        )
        catalog = state["app_state"].connection_catalog
        try:
            conn = await catalog.update(
                name,
                base_url=base_url,
                metadata=metadata,
                health_check_enabled=health_check_enabled,
                webhook_receipt_retention_days=webhook_receipt_retention_days,
            )
        except ConnectionNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                connection=name,
                operation="update",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise NotFoundError(str(exc)) from exc
        logger.info(
            SECURITY_CONNECTION_UPDATED,
            connection=name,
            fields_changed=sorted(data.model_fields_set),
        )
        return ApiResponse(data=conn)

    @delete(
        "/{name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("connections.delete", key="user"),
        ],
        summary="Delete a connection",
        status_code=200,
    )
    async def delete_connection(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[None]:
        """Delete a connection and its secrets."""
        catalog = state["app_state"].connection_catalog
        try:
            await catalog.delete(name)
        except ConnectionNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                connection=name,
                operation="delete",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise NotFoundError(str(exc)) from exc
        logger.info(
            SECURITY_CONNECTION_DELETED,
            connection=name,
        )
        return ApiResponse(data=None)

    @get(
        "/{name:str}/health",
        guards=[require_read_access],
        summary="Check connection health",
    )
    async def check_health(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[HealthReport]:
        """Run an on-demand health check for a connection."""
        from synthorg.integrations.health.service import (  # noqa: PLC0415
            check_connection_health,
        )

        catalog = state["app_state"].connection_catalog
        try:
            report = await check_connection_health(catalog, name)
            # ``update_health`` shares the 404 mapping: a concurrent
            # delete between the probe and the health write would
            # otherwise bubble a ``ConnectionNotFoundError`` as a 500
            # and skip the structured ``API_RESOURCE_NOT_FOUND`` log.
            await catalog.update_health(
                name,
                status=report.status,
                checked_at=report.checked_at,
            )
        except ConnectionNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                connection=name,
                operation="health_check",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise NotFoundError(str(exc)) from exc
        return ApiResponse(data=report)

    @get(
        "/{name:str}/secrets/{field:str}",
        guards=[require_write_access],
        summary="Reveal a single credential field",
    )
    async def reveal_secret(
        self,
        state: State,
        name: PathName,
        field: PathField,
    ) -> ApiResponse[dict[str, str]]:
        """Return the plaintext value of one credential field.

        Scoped to a single field so a reveal action on the OAuth
        Apps page can surface a specific ``client_secret`` without
        exposing the rest of the credential blob. The reveal is
        audit-logged (field name only, never the value).
        """
        catalog = state["app_state"].connection_catalog
        try:
            credentials = await catalog.get_credentials(name)
        except ConnectionNotFoundError as exc:
            logger.warning(
                SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
                connection=name,
                field=field,
                reason="connection_not_found",
            )
            raise NotFoundError(_REVEAL_GENERIC_ERROR) from exc
        except SecretRetrievalError as exc:
            # Secret backend failures are operational errors, not a
            # "not found" condition -- log at ERROR level so they
            # show up on the health dashboard instead of getting lost
            # in the 404 noise.  Use the request-side reveal-failed
            # event rather than the backend-side
            # ``SECRET_RETRIEVAL_FAILED`` that the catalog already
            # emitted -- otherwise one backend failure would
            # double-count and the user-visible context (this is a
            # reveal request, not a credential-resolve) would be lost.
            # ``exc_info`` is intentionally omitted: the full traceback
            # for a credential-bearing operation can leak backend
            # secret metadata via wrapped causes; the redacted
            # ``safe_error_description`` is the only message emitted.
            logger.error(  # noqa: TRY400
                SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
                connection=name,
                field=field,
                reason="secret_retrieval_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise NotFoundError(_REVEAL_GENERIC_ERROR) from exc

        value = credentials.get(field)
        if value is None:
            logger.warning(
                SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
                connection=name,
                field=field,
                reason="field_not_set",
            )
            raise NotFoundError(_REVEAL_GENERIC_ERROR)
        logger.info(
            SECURITY_CONNECTION_SECRET_REVEALED,
            connection=name,
            field=field,
        )
        return ApiResponse(data={"field": field, "value": value})
