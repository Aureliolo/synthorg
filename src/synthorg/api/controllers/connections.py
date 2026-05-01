"""Connections API controller.

CRUD endpoints for the external service connection catalog,
including on-demand health checks.
"""

from typing import Annotated

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.types import (
    NotBlankStr,  # noqa: TC001 -- Pydantic field annotation evaluated at runtime
)
from synthorg.integrations.connections.catalog import _UNSET
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
from synthorg.observability.events.integrations import (
    CONNECTION_SECRET_REVEAL_FAILED,
    CONNECTION_SECRET_REVEALED,
    SECRET_RETRIEVAL_FAILED,
)

# Length caps applied at the API boundary to prevent unbounded
# string allocation on attacker-controllable inputs (#1682). The
# credential / metadata key caps share the same value as the
# connection-name cap, but the *names* are kept distinct so a future
# tuner who needs to widen credential keys (e.g. for tokens with
# embedded scopes) does not have to disentangle the connection-name
# semantic from the dict-key one.
_MAX_NAME_LEN = 128
_MAX_BASE_URL_LEN = 2048
_MAX_CRED_KEY_LEN = 128
_MAX_CRED_VALUE_LEN = 8192
_MAX_METADATA_KEY_LEN = 128
_MAX_METADATA_VALUE_LEN = 4096


class CreateConnectionRequest(BaseModel):
    """Body model for ``POST /connections``.

    Replaces the prior ``data: dict[str, Any]`` shape so input
    validation runs at the boundary and unbounded strings are
    rejected before reaching the catalog (#1682).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    name: Annotated[NotBlankStr, Field(max_length=_MAX_NAME_LEN)]
    connection_type: ConnectionType
    auth_method: AuthMethod = AuthMethod.API_KEY
    # ``NotBlankStr`` keys reject ``""`` and whitespace-only strings
    # so an attacker can't slip a blank-keyed credential past the DTO
    # (#1682, CodeRabbit at connections.py:77). Empty credential keys
    # are never legitimate -- the catalog later normalises by name.
    credentials: dict[
        Annotated[NotBlankStr, Field(max_length=_MAX_CRED_KEY_LEN)],
        Annotated[str, Field(max_length=_MAX_CRED_VALUE_LEN)],
    ] = Field(default_factory=dict)
    base_url: Annotated[str, Field(max_length=_MAX_BASE_URL_LEN)] | None = None
    metadata: (
        dict[
            Annotated[NotBlankStr, Field(max_length=_MAX_METADATA_KEY_LEN)],
            Annotated[str, Field(max_length=_MAX_METADATA_VALUE_LEN)],
        ]
        | None
    ) = None
    health_check_enabled: bool = True


class UpdateConnectionRequest(BaseModel):
    """Body model for ``PATCH /connections/{name}``.

    Optional fields with three-way semantics on ``base_url``:

    * **omitted** (``base_url`` not in ``model_fields_set``): leave
      the stored value unchanged.
    * **explicit ``None``** (``"base_url": null`` in the JSON body):
      clear the stored value.
    * **string**: overwrite.

    The controller distinguishes the omitted vs explicit-null cases
    by inspecting ``data.model_fields_set`` (see
    :meth:`ConnectionsController.update_connection`) and forwards the
    sentinel ``_UNSET`` to the catalog when the field was omitted.
    Pydantic + ``extra="forbid"`` rejects unknown fields entirely.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    base_url: Annotated[str, Field(max_length=_MAX_BASE_URL_LEN)] | None = None
    metadata: (
        dict[
            Annotated[NotBlankStr, Field(max_length=_MAX_METADATA_KEY_LEN)],
            Annotated[str, Field(max_length=_MAX_METADATA_VALUE_LEN)],
        ]
        | None
    ) = None
    health_check_enabled: bool | None = None


# Unified error surfaced to clients on any reveal failure. The
# message is deliberately opaque so callers cannot distinguish
# "connection missing" from "field missing" from "secret backend
# unavailable" -- all three would otherwise leak side-channel
# information about what connections exist and which fields are set.
_REVEAL_GENERIC_ERROR = "Connection or credential field not found"

logger = get_logger(__name__)


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
        name: str = Parameter(
            description="Connection name",
            max_length=_MAX_NAME_LEN,
        ),
    ) -> ApiResponse[Connection]:
        """Get a single connection by name."""
        catalog = state["app_state"].connection_catalog
        conn = await catalog.get(name)
        if conn is None:
            msg = f"Connection '{name}' not found"
            raise NotFoundError(msg) from None
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

        Pydantic validates the body against
        :class:`CreateConnectionRequest` (frozen, ``extra="forbid"``,
        per-field length caps) so any malformed payload surfaces as a
        structured 422 from Litestar's exception handler before this
        method runs.
        """
        # Persist the canonical trimmed form so "  github  " and
        # "github" cannot become two distinct identities and so the
        # /{name} routes consistently address the stored row.
        name = data.name.strip()
        # Defensive copies of the mutable mapping fields. ``frozen=True``
        # on the DTO does not deep-freeze nested dicts, so passing
        # ``data.credentials`` / ``data.metadata`` by reference would let
        # the catalog mutate the request DTO's storage in place and
        # break the immutability contract (CodeRabbit at
        # connections.py:231 + CLAUDE.md "Create new objects, never
        # mutate existing ones").
        credentials = dict(data.credentials)
        metadata = None if data.metadata is None else dict(data.metadata)
        catalog = state["app_state"].connection_catalog
        try:
            conn = await catalog.create(
                name=name,
                connection_type=data.connection_type,
                auth_method=data.auth_method.value,
                credentials=credentials,
                base_url=data.base_url,
                metadata=metadata,
                health_check_enabled=data.health_check_enabled,
            )
        except DuplicateConnectionError as exc:
            raise ConflictError(str(exc)) from exc
        except InvalidConnectionAuthError as exc:
            raise ValidationError(str(exc)) from exc
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
        data: UpdateConnectionRequest,
        name: str = Parameter(
            description="Connection name",
            max_length=_MAX_NAME_LEN,
        ),
    ) -> ApiResponse[Connection]:
        """Update mutable fields of a connection.

        Pydantic enforces shape and length on the request body.
        ``base_url`` distinguishes "omitted" (leave unchanged) from
        "explicitly null" (clear the URL): we read
        ``data.model_fields_set`` to detect omission and forward the
        sentinel ``_UNSET`` to the catalog.
        """
        catalog = state["app_state"].connection_catalog
        base_url_arg = data.base_url if "base_url" in data.model_fields_set else _UNSET
        # Defensive copy: see ``create_connection`` for the
        # immutability rationale (#1682).
        metadata = None if data.metadata is None else dict(data.metadata)
        try:
            conn = await catalog.update(
                name,
                base_url=base_url_arg,
                metadata=metadata,
                health_check_enabled=data.health_check_enabled,
            )
        except ConnectionNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
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
        name: str = Parameter(
            description="Connection name",
            max_length=_MAX_NAME_LEN,
        ),
    ) -> ApiResponse[None]:
        """Delete a connection and its secrets."""
        catalog = state["app_state"].connection_catalog
        try:
            await catalog.delete(name)
        except ConnectionNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        return ApiResponse(data=None)

    @get(
        "/{name:str}/health",
        guards=[require_read_access],
        summary="Check connection health",
    )
    async def check_health(
        self,
        state: State,
        name: str = Parameter(
            description="Connection name",
            max_length=_MAX_NAME_LEN,
        ),
    ) -> ApiResponse[HealthReport]:
        """Run an on-demand health check for a connection."""
        from synthorg.integrations.health.service import (  # noqa: PLC0415
            check_connection_health,
        )

        catalog = state["app_state"].connection_catalog
        try:
            report = await check_connection_health(catalog, name)
        except ConnectionNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        await catalog.update_health(
            name,
            status=report.status,
            checked_at=report.checked_at,
        )
        return ApiResponse(data=report)

    @get(
        "/{name:str}/secrets/{field:str}",
        guards=[require_write_access],
        summary="Reveal a single credential field",
    )
    async def reveal_secret(
        self,
        state: State,
        name: str = Parameter(
            description="Connection name",
            max_length=_MAX_NAME_LEN,
        ),
        field: str = Parameter(
            description="Credential field name",
            max_length=_MAX_NAME_LEN,
        ),
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
                CONNECTION_SECRET_REVEAL_FAILED,
                connection_name=name,
                field=field,
                reason="connection_not_found",
            )
            raise NotFoundError(_REVEAL_GENERIC_ERROR) from exc
        except SecretRetrievalError as exc:
            # Secret backend failures are operational errors, not a
            # "not found" condition -- log at ERROR level so they
            # show up on the health dashboard instead of getting lost
            # in the 404 noise.
            logger.error(  # noqa: TRY400
                SECRET_RETRIEVAL_FAILED,
                connection_name=name,
                field=field,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise NotFoundError(_REVEAL_GENERIC_ERROR) from exc

        value = credentials.get(field)
        if value is None:
            logger.warning(
                CONNECTION_SECRET_REVEAL_FAILED,
                connection_name=name,
                field=field,
                reason="field_not_set",
            )
            raise NotFoundError(_REVEAL_GENERIC_ERROR)
        logger.info(
            CONNECTION_SECRET_REVEALED,
            connection_name=name,
            field=field,
        )
        return ApiResponse(data={"field": field, "value": value})
