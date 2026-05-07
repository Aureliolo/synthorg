"""MCP catalog API controller.

Browse and install MCP servers from the bundled catalog.
"""

from typing import Literal

from litestar import Controller, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    paginate_cursor,
)
from synthorg.api.path_params import PathId  # noqa: TC001 -- runtime annotation
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.connections.models import CatalogEntry  # noqa: TC001
from synthorg.integrations.errors import (
    InvalidConnectionAuthError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALL_FAILED,
    MCP_SERVER_UNINSTALL_NOOP,
)

logger = get_logger(__name__)


class InstallEntryRequest(BaseModel):
    """Request body for ``POST /catalog/install``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    catalog_entry_id: NotBlankStr = Field(
        description="Catalog entry identifier to install",
    )
    connection_name: NotBlankStr | None = Field(
        default=None,
        description=(
            "Bound connection name; required when the entry declares a "
            "``required_connection_type``"
        ),
    )


class InstallEntryResponse(BaseModel):
    """Response body for ``POST /catalog/install``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: Literal["installed"] = Field(description="Installation status")
    server_name: NotBlankStr = Field(description="Installed MCP server name")
    catalog_entry_id: NotBlankStr = Field(description="Installed catalog entry id")
    tool_count: int = Field(
        ge=0,
        description="Number of tools exposed by the installed server",
    )


async def _validate_connection_name_for_install(
    *,
    entry_id: str,
    connection_name: str | None,
    connection_catalog: object | None,
) -> None:
    """Validate ``connection_name`` exists before the install INSERT.

    Pre-validation closes the gap where an unknown ``connection_name``
    would otherwise reach the FK column on ``mcp_installations`` and
    raise ``psycopg.errors.ForeignKeyViolation`` outside the service's
    typed ``ConnectionNotFoundError`` arm. The
    service's post-call check still validates the
    ``required_connection_type`` branch; this gate covers the
    no-required-type-but-unknown-name path. The IntegrityError
    backstop in ``api/exception_handlers.py`` covers the racy
    "connection deleted between validate and INSERT" window.

    Args:
        entry_id: Catalog entry being installed (used for log context).
        connection_name: Name supplied by the caller, or ``None`` to
            skip validation.
        connection_catalog: Resolved catalog (or ``None`` when the
            integrations subsystem is unconfigured).

    Raises:
        ValidationError: When ``connection_name`` is supplied but the
            catalog is missing or the name does not resolve.
    """
    if connection_name is None:
        return
    if connection_catalog is None:
        msg = "Integrations subsystem is not configured; cannot bind connection_name."
        logger.warning(
            MCP_SERVER_INSTALL_FAILED,
            entry_id=entry_id,
            connection_name=connection_name,
            reason="connection_catalog_unavailable",
        )
        raise ValidationError(msg)
    # Static-type-friendly: ``connection_catalog`` carries the
    # ``ConnectionCatalog`` protocol but is typed as ``object`` here
    # because the controller hands it through unchanged. The runtime
    # ``get(name) -> Connection | None`` contract is the only thing
    # we rely on; treating it as object keeps the helper free of an
    # otherwise-unused import.
    existing = await connection_catalog.get(connection_name)  # type: ignore[attr-defined]
    if existing is None:
        msg = f"unknown connection {connection_name!r}"
        logger.warning(
            MCP_SERVER_INSTALL_FAILED,
            entry_id=entry_id,
            connection_name=connection_name,
            reason="connection_not_found_pre_install",
        )
        raise ValidationError(msg)


class MCPCatalogController(Controller):
    """Browse and install MCP servers from the bundled catalog."""

    path = "/integrations/mcp"
    tags = ["Integrations"]  # noqa: RUF012

    @get(
        "/catalog",
        guards=[require_read_access],
        summary="List all catalog entries",
    )
    async def browse_catalog(
        self,
        state: State,
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[CatalogEntry]:
        """List all curated MCP server entries (cursor-paginated)."""
        app_state = state["app_state"]
        entries = await app_state.mcp_catalog_service.browse()
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get(
        "/catalog/search",
        guards=[require_read_access],
        summary="Search catalog entries",
    )
    async def search_catalog(
        self,
        state: State,
        q: str = Parameter(
            description="Search query",
            max_length=512,
        ),
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[CatalogEntry]:
        """Search catalog by name, description, or tags (cursor-paginated)."""
        app_state = state["app_state"]
        entries = await app_state.mcp_catalog_service.search(q)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get(
        "/catalog/{entry_id:str}",
        guards=[require_read_access],
        summary="Get a catalog entry",
    )
    async def get_entry(
        self,
        state: State,
        entry_id: PathId,
    ) -> ApiResponse[CatalogEntry]:
        """Get a single catalog entry by ID.

        ``CatalogEntryNotFoundError`` propagates directly to the
        central exception handler (its class-level ``status_code``
        / ``error_code`` map it to 404 + ``RECORD_NOT_FOUND``).  The
        previous controller-level translation collapsed the type
        into the generic ``NotFoundError`` and lost the discriminating
        envelope.
        """
        service = state["app_state"].mcp_catalog_service
        entry = await service.get_entry(entry_id)
        return ApiResponse(data=entry)

    @post(
        "/catalog/install",
        guards=[require_write_access],
        summary="Install a catalog entry",
    )
    async def install_entry(
        self,
        state: State,
        data: InstallEntryRequest,
    ) -> ApiResponse[InstallEntryResponse]:
        """Record an installation of a bundled MCP catalog entry.

        Validates the entry exists, that the bound connection (if
        required) matches the entry's ``required_connection_type``,
        and persists the row so the MCP bridge picks it up on next
        reload. Re-installing an existing entry is idempotent.
        """
        entry_id = data.catalog_entry_id
        connection_name = data.connection_name

        app_state = state["app_state"]
        service = app_state.mcp_catalog_service
        installations_repo = app_state.mcp_installations_repo
        connection_catalog = (
            app_state.connection_catalog if app_state.has_connection_catalog else None
        )

        await _validate_connection_name_for_install(
            entry_id=entry_id,
            connection_name=connection_name,
            connection_catalog=connection_catalog,
        )

        try:
            result = await service.install(
                entry_id,
                connection_name,
                connection_catalog=connection_catalog,
                installations_repo=installations_repo,
            )
        except InvalidConnectionAuthError as exc:
            logger.warning(
                MCP_SERVER_INSTALL_FAILED,
                entry_id=entry_id,
                connection_name=connection_name,
                reason="connection_type_mismatch",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(str(exc)) from exc

        # NB: we intentionally don't re-log ``MCP_SERVER_INSTALLED``
        # here - the repository's ``save()`` is the canonical audit
        # point and logs the same event with a ``backend`` tag.
        return ApiResponse(
            data=InstallEntryResponse(
                status="installed",
                server_name=result.server_name,
                catalog_entry_id=result.catalog_entry_id,
                tool_count=result.tool_count,
            ),
        )

    @delete(
        "/catalog/install/{entry_id:str}",
        guards=[require_write_access],
        summary="Uninstall a catalog entry",
        status_code=200,
    )
    async def uninstall_entry(
        self,
        state: State,
        entry_id: PathId,
    ) -> ApiResponse[None]:
        """Remove a recorded installation.

        Missing entries are a silent no-op so the endpoint is
        idempotent and callers can always treat 200 as success.
        """
        app_state = state["app_state"]
        service = app_state.mcp_catalog_service
        installations_repo = app_state.mcp_installations_repo
        removed = await service.uninstall(
            entry_id,
            installations_repo=installations_repo,
        )
        if not removed:
            # The repo-level ``MCP_SERVER_UNINSTALLED`` event is only
            # emitted when a row was actually deleted. Log a distinct
            # no-op event so idempotent DELETE calls are still visible
            # in audit trails without being confused with real removals.
            logger.info(
                MCP_SERVER_UNINSTALL_NOOP,
                catalog_entry_id=entry_id,
            )
        return ApiResponse(data=None)
