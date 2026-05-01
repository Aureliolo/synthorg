"""Connections API controller.

CRUD endpoints for the external service connection catalog,
including on-demand health checks.
"""

from typing import Any

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.integrations.connections.catalog import _UNSET
from synthorg.integrations.connections.models import (
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
from synthorg.observability.events.integrations import (
    CONNECTION_SECRET_REVEAL_FAILED,
    CONNECTION_SECRET_REVEALED,
    SECRET_RETRIEVAL_FAILED,
)

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
        name: str = Parameter(description="Connection name"),
    ) -> ApiResponse[Connection]:
        """Get a single connection by name."""
        catalog = state["app_state"].connection_catalog
        conn = await catalog.get(name)
        if conn is None:
            msg = f"Connection '{name}' not found"
            logger.warning(API_RESOURCE_NOT_FOUND, connection=name, reason=msg)
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
        data: dict[str, Any],
    ) -> ApiResponse[Connection]:
        """Create a new connection.

        Validates required fields and connection type before
        delegating to the catalog so clients get a structured
        400 instead of a 500 on malformed payloads.
        """
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            msg = "Field 'name' is required and must be a non-empty string"
            logger.warning(API_VALIDATION_FAILED, field="name", reason=msg)
            raise ValidationError(msg)
        # Persist the canonical trimmed form so "  github  " and
        # "github" cannot become two distinct identities and so the
        # /{name} routes consistently address the stored row.
        name = name.strip()

        connection_type_raw = data.get("connection_type")
        if not isinstance(connection_type_raw, str) or not connection_type_raw:
            msg = "Field 'connection_type' is required"
            logger.warning(
                API_VALIDATION_FAILED,
                field="connection_type",
                reason=msg,
            )
            raise ValidationError(msg)
        try:
            connection_type = ConnectionType(connection_type_raw)
        except ValueError as exc:
            msg = f"Unknown connection_type '{connection_type_raw}'"
            logger.warning(
                API_VALIDATION_FAILED,
                field="connection_type",
                value=connection_type_raw,
                reason=msg,
            )
            raise ValidationError(msg) from exc

        credentials = data.get("credentials", {})
        if not isinstance(credentials, dict):
            msg = "Field 'credentials' must be an object"
            logger.warning(API_VALIDATION_FAILED, field="credentials", reason=msg)
            raise ValidationError(msg)

        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            msg = "Field 'metadata' must be an object if provided"
            logger.warning(API_VALIDATION_FAILED, field="metadata", reason=msg)
            raise ValidationError(msg)

        health_check_enabled = data.get("health_check_enabled", True)
        if not isinstance(health_check_enabled, bool):
            msg = "Field 'health_check_enabled' must be a boolean"
            logger.warning(
                API_VALIDATION_FAILED,
                field="health_check_enabled",
                reason=msg,
            )
            raise ValidationError(msg)

        catalog = state["app_state"].connection_catalog
        try:
            conn = await catalog.create(
                name=name,
                connection_type=connection_type,
                auth_method=data.get("auth_method", "api_key"),
                credentials=credentials,
                base_url=data.get("base_url"),
                metadata=metadata,
                health_check_enabled=health_check_enabled,
            )
        except DuplicateConnectionError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                connection=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        except InvalidConnectionAuthError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                connection=name,
                field="auth_method",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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
        name: str,
        data: dict[str, Any],
    ) -> ApiResponse[Connection]:
        """Update mutable fields of a connection."""
        # Validate PATCH field types at the boundary so malformed
        # payloads surface as a structured 400 instead of failing
        # inside the catalog / Pydantic model layer.
        if "base_url" in data:
            base_url_value = data["base_url"]
            if base_url_value is not None and not isinstance(base_url_value, str):
                msg = "Field 'base_url' must be a string or null"
                raise ValidationError(msg)
        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            msg = "Field 'metadata' must be an object if provided"
            raise ValidationError(msg)
        health_check_enabled = data.get("health_check_enabled")
        if health_check_enabled is not None and not isinstance(
            health_check_enabled,
            bool,
        ):
            msg = "Field 'health_check_enabled' must be a boolean if provided"
            raise ValidationError(msg)

        catalog = state["app_state"].connection_catalog
        try:
            conn = await catalog.update(
                name,
                base_url=data.get("base_url", _UNSET),
                metadata=metadata,
                health_check_enabled=health_check_enabled,
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
        name: str,
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
        return ApiResponse(data=None)

    @get(
        "/{name:str}/health",
        guards=[require_read_access],
        summary="Check connection health",
    )
    async def check_health(
        self,
        state: State,
        name: str,
    ) -> ApiResponse[HealthReport]:
        """Run an on-demand health check for a connection."""
        from synthorg.integrations.health.service import (  # noqa: PLC0415
            check_connection_health,
        )

        catalog = state["app_state"].connection_catalog
        try:
            report = await check_connection_health(catalog, name)
        except ConnectionNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                connection=name,
                operation="health_check",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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
        name: str,
        field: str,
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
            # in the 404 noise.  ``exc_info`` is intentionally omitted:
            # the full traceback for a credential-bearing operation can
            # leak backend secret metadata via wrapped causes; the
            # redacted ``safe_error_description`` is the only message
            # emitted.
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
