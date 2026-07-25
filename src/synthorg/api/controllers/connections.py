"""Connections API controller.

CRUD endpoints for the external service connection catalog,
including on-demand health checks.
"""

import copy
from typing import Final

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers._connection_secrets import (
    capture_secret_value,
    resolve_create_credentials,
    reveal_secret_field,
)
from synthorg.api.controllers.connections_models import (
    CreateConnectionRequest,
    RevealedSecretResponse,
    SecretCaptureRequest,
    SecretCaptureResponse,
    UpdateConnectionRequest,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathField, PathId, PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeAccessibleRepo,
)
from synthorg.integrations.connections.catalog import _UNSET, _UnsetType
from synthorg.integrations.connections.field_metadata import (
    ConnectionTypeMetadata,
    list_connection_type_metadata,
)
from synthorg.integrations.connections.models import (
    Connection,
    HealthReport,
)
from synthorg.integrations.errors import ConnectionNotFoundError
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import (
    get_logger,
    safe_error_description,
)
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.observability.events.security import (
    SECURITY_CONNECTION_CREATED,
    SECURITY_CONNECTION_DELETED,
    SECURITY_CONNECTION_UPDATED,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

__all__ = [
    "ConnectionsController",
    "CreateConnectionRequest",
    "UpdateConnectionRequest",
]


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
        limit: CursorLimit = _DEFAULT_LIMIT,
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
        catalog = require_service(
            app_state.slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
        connections = await catalog.list_all()
        page, meta = paginate_cursor(
            tuple(connections),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[Connection](data=page, pagination=meta)

    @get(
        "/types",
        guards=[require_read_access],
        summary="Connection-type field metadata registry",
    )
    async def list_connection_types(
        self,
    ) -> ApiResponse[list[ConnectionTypeMetadata]]:
        """Return the connection-type + credential-field metadata registry.

        The single source of truth (`connections/field_metadata.py`) the
        operator-console setup flow prompts from and the dashboard connection
        form renders, so both agree on labels, types, required/secret flags,
        capture mode, and field ordering without per-type UI code.

        Returns:
            ``ApiResponse`` wrapping the ordered per-type field metadata.
        """
        return ApiResponse(data=list(list_connection_type_metadata()))

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
        """Get a single connection by name.

        Returns:
            ``ApiResponse[Connection]`` instance.
        """
        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
        conn = require_resource_or_404(
            await catalog.get(name),
            resource_type="Connection",
            identifier=name,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"connection": name},
            error_class=ConnectionNotFoundError,
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
        response automatically.  The handler body therefore only owns the
        persistence-layer dispatch; the domain errors it can raise carry
        their own wire contract on the class and propagate untouched to the
        central handler rather than being caught and re-mapped here.

        Returns:
            ``ApiResponse[Connection]`` instance.

        Raises:
            DuplicateConnectionError: If a connection with this name already
                exists (409, mapped by the domain handler from class metadata).
            InvalidConnectionAuthError: If the supplied credentials fail
                validation (422, mapped by the domain handler).
        """
        # Resolve any out-of-band secret handles to their raw values in-process
        # (secret fields never arrive inline in the request body); inline
        # non-secret fields pass through. The returned mapping is a fresh dict,
        # so no shared-reference write can reach the caller's payload. ``metadata``
        # is still caller-owned, so it is defensively deepcopied.
        credentials_copy = await resolve_create_credentials(state["app_state"], data)
        metadata_copy = (
            copy.deepcopy(data.metadata) if data.metadata is not None else None
        )
        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
        # ``DuplicateConnectionError`` (409) and ``InvalidConnectionAuthError``
        # (422, raised by ``authenticator.validate_credentials``) carry the
        # right wire contract on the class, so they propagate untouched to the
        # central handler instead of being caught and re-mapped.
        conn = await catalog.create(
            name=data.name,
            connection_type=data.connection_type,
            auth_method=data.auth_method.value,
            credentials=credentials_copy,
            base_url=data.base_url,
            metadata=metadata_copy,
            health_check_enabled=data.health_check_enabled,
            webhook_receipt_retention_days=data.webhook_receipt_retention_days,
            sensitive=data.sensitive,
            allowed_repos=tuple(str(r) for r in data.allowed_repos),
        )
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

        Returns:
            ``ApiResponse[Connection]`` instance.

        Raises:
            ConnectionNotFoundError: Raised on the corresponding failure path.
        """
        # ``model_fields_set`` distinguishes "field omitted" from "field
        # explicitly set to ``None``" so a PATCH that drops ``base_url``
        # can still null out the stored value via ``base_url=None``;
        # when the field was omitted we forward ``_UNSET`` to keep the
        # catalog's existing value.  All four mutable fields use the
        # same semantic so client behaviour is uniform.
        base_url: str | _UnsetType | None = (
            data.base_url if "base_url" in data.model_fields_set else _UNSET
        )
        metadata: dict[str, str] | _UnsetType | None
        if "metadata" in data.model_fields_set:
            # Defensively deepcopy when provided; same reasoning as
            # ``create_connection`` (catalog briefly holds the mapping
            # before persisting / encrypting nested secrets).
            metadata = (
                copy.deepcopy(data.metadata) if data.metadata is not None else None
            )
        else:
            metadata = _UNSET
        health_check_enabled: bool | _UnsetType | None = (
            data.health_check_enabled
            if "health_check_enabled" in data.model_fields_set
            else _UNSET
        )
        webhook_receipt_retention_days: int | _UnsetType | None = (
            data.webhook_receipt_retention_days
            if "webhook_receipt_retention_days" in data.model_fields_set
            else _UNSET
        )
        # ``_reject_null_sensitive`` guarantees a set value is never None, so
        # ``bool(...)`` is just type narrowing for the catalog signature.
        sensitive: bool | _UnsetType = (
            bool(data.sensitive) if "sensitive" in data.model_fields_set else _UNSET
        )
        # An explicit ``allowed_repos: []`` clears the scope (deny-all); an
        # omitted field keeps the stored scope. The request validator refuses
        # an explicit null, so a set value is never None here.
        allowed_repos: tuple[str, ...] | _UnsetType = (
            tuple(str(r) for r in (data.allowed_repos or ()))
            if "allowed_repos" in data.model_fields_set
            else _UNSET
        )
        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
        try:
            conn = await catalog.update(
                name,
                base_url=base_url,
                metadata=metadata,
                health_check_enabled=health_check_enabled,
                webhook_receipt_retention_days=webhook_receipt_retention_days,
                sensitive=sensitive,
                allowed_repos=allowed_repos,
            )
        except ConnectionNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                connection=name,
                operation="update",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Let the domain error propagate so EXCEPTION_HANDLERS
            # routes it through the ConnectionNotFoundError envelope
            # (404 + ``CONNECTION_NOT_FOUND``) rather than collapsing
            # it into the generic NotFoundError shape.
            raise
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
        """Delete a connection and its secrets.

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            ConnectionNotFoundError: Raised on the corresponding failure path.
        """
        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
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
            # Same rationale as ``update_connection`` above -- preserve
            # the ConnectionNotFoundError envelope.
            raise
        logger.info(
            SECURITY_CONNECTION_DELETED,
            connection=name,
        )
        return ApiResponse(data=None)

    @post(
        "/drafts/{draft_id:str}/fields/{field:str}/capture",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("connections.create", key="user"),
        ],
        summary="Capture a credential value out of band (write-only)",
    )
    async def capture_secret(
        self,
        state: State,
        draft_id: PathId,
        field: PathField,
        data: SecretCaptureRequest,
    ) -> ApiResponse[SecretCaptureResponse]:
        """Capture a credential value out of band and return an opaque handle.

        The raw value is written straight to the secret backend and never
        enters the conversation transcript, an LLM prompt, or the logs; the
        returned single-use handle is consumed once by ``connections.create``.
        Implementation lives in ``_connection_secrets``.

        Returns:
            ``ApiResponse[SecretCaptureResponse]`` wrapping the opaque handle.
        """
        captured = await capture_secret_value(state["app_state"], draft_id, field, data)
        return ApiResponse(data=captured)

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
        """Run an on-demand health check for a connection.

        Returns:
            ``ApiResponse[HealthReport]`` instance.

        Raises:
            ConnectionNotFoundError: Raised on the corresponding failure path.
        """
        from synthorg.integrations.health.service import (  # noqa: PLC0415
            check_connection_health,
        )

        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
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
            # Same rationale as ``update_connection`` above -- preserve
            # the ConnectionNotFoundError envelope so a concurrent
            # delete during health-probe surfaces with the correct
            # ``CONNECTION_NOT_FOUND`` error code.
            raise
        return ApiResponse(data=report)

    @get(
        "/{name:str}/accessible-repos",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("connections.accessible_repos", key="user"),
        ],
        summary="Scan a forge connection's accessible repositories",
    )
    async def scan_accessible_repos(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[list[ForgeAccessibleRepo]]:
        """List the repositories the forge connection's token can reach.

        Powers the operator repo-scope picker: the returned repositories
        are the candidates an operator selects into the connection's
        ``allowed_repos`` scope. Egress is pinned to the connection host by
        construction. Write-guarded and per-user rate-limited because it
        brokers the connection's credentials into a live outbound call to
        the forge on every request.

        Returns:
            ``ApiResponse`` wrapping the accessible repositories.

        Raises:
            ConnectionNotFoundError: When no such connection exists.
            ForgeUnsupportedError: When the connection is not a forge.
        """
        from synthorg.integrations.connections.forge_scan import (  # noqa: PLC0415
            scan_accessible_repos,
        )

        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
        repos = await scan_accessible_repos(catalog, name)
        return ApiResponse(data=list(repos))

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
    ) -> ApiResponse[RevealedSecretResponse]:
        """Return the plaintext value of one credential field.

        Scoped to a single field so a reveal action on the OAuth Apps page
        can surface a specific ``client_secret`` without exposing the rest
        of the credential blob. The implementation (uniform-404 on any miss,
        audit by field name only) lives in ``_connection_secrets`` to keep
        this controller within its size budget.

        Returns:
            ``ApiResponse[RevealedSecretResponse]`` instance.

        Raises:
            SecretRetrievalNotFoundError: For a missing connection, an unset
                field, or a secret-backend failure (uniform 404).
        """
        revealed = await reveal_secret_field(state["app_state"], name, field)
        return ApiResponse(data=revealed)
