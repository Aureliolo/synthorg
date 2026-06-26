"""MCP catalog API controller.

Browse and install MCP servers from the bundled catalog.
"""

from typing import Annotated, Final, Literal

from litestar import Controller, delete, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import CatalogEntry
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallation,
)
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    MCP_BRIDGE_RELOAD_FAILED,
    MCP_BRIDGE_RELOADED,
    MCP_SERVER_INSTALL_FAILED,
    MCP_SERVER_UNINSTALL_NOOP,
)

logger = get_logger(__name__)


async def _reload_bridge_best_effort(
    app_state: AppState, *, action: str, catalog_entry_id: str
) -> None:
    """Hot-reload the agent runtime so an MCP catalog change goes live now.

    The calling controller has already committed the install/uninstall row,
    so a reload failure must NOT fail the request: the change still applies
    on the next natural runtime rebuild. We rebuild + hot-swap proactively
    (no manual reload / restart) and log the outcome, tagging the catalog
    entry so a failure correlates with the install/uninstall that triggered
    it.
    """
    from synthorg.core.critical_errors import reraise_critical  # noqa: PLC0415
    from synthorg.workers.runtime_builder import (  # noqa: PLC0415
        reload_runtime_services,
    )

    try:
        await reload_runtime_services(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MCP_BRIDGE_RELOAD_FAILED,
            action=action,
            catalog_entry_id=catalog_entry_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(MCP_BRIDGE_RELOADED, action=action, catalog_entry_id=catalog_entry_id)


# Page size for draining the installed-MCP-entries repo before
# cursor-paginating the response. Larger pages mean fewer round-trips
# on big install sets; the page boundary itself is irrelevant to the
# response shape because the controller drains every page.
_LIST_PAGE_SIZE: Final[int] = 500


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


class InstalledEntry(BaseModel):
    """One row of the installed-MCP-entries listing."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    catalog_entry_id: NotBlankStr = Field(description="Installed catalog entry id")
    connection_name: NotBlankStr | None = Field(
        default=None,
        description="Bound connection name (when applicable)",
    )
    installed_at: str = Field(description="ISO-8601 UTC timestamp of installation")


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
        """List all curated MCP server entries (cursor-paginated).

        Returns:
            ``PaginatedResponse[CatalogEntry]`` instance.
        """
        app_state: AppState = state.app_state
        service = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_catalog_service,
            "MCP Catalog Service",
        )
        entries = await service.browse()
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get(
        "/catalog/search",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy(
                "integrations.mcp_catalog_search", key="user"
            ),
        ],
        summary="Search catalog entries",
    )
    async def search_catalog(
        self,
        state: State,
        q: Annotated[
            str,
            QueryParameter(
                description="Search query",
                max_length=512,
            ),
        ],
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[CatalogEntry]:
        """Search catalog by name, description, or tags (cursor-paginated).

        Returns:
            ``PaginatedResponse[CatalogEntry]`` instance.
        """
        app_state: AppState = state.app_state
        service = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_catalog_service,
            "MCP Catalog Service",
        )
        entries = await service.search(q)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
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

        ``CatalogEntryNotFoundError`` propagates directly to the central
        exception handler, whose class-level ``status_code`` / ``error_code``
        map it to 404 + ``RECORD_NOT_FOUND`` so the response keeps the
        discriminating error envelope rather than a generic not-found.

        Returns:
            ``ApiResponse[CatalogEntry]`` instance.
        """
        app_state: AppState = state.app_state
        service = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_catalog_service,
            "MCP Catalog Service",
        )
        entry = await service.get_entry(entry_id)
        return ApiResponse(data=entry)

    @get(
        "/catalog/installed",
        guards=[require_read_access],
        summary="List installed catalog entries",
    )
    async def list_installed(
        self,
        state: State,
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[InstalledEntry]:
        """List MCP catalog entries currently installed on this instance.

        Backs the dashboard's installed-state badge: it rehydrates from
        this endpoint on each page load, so the installed set survives a
        refresh instead of depending on transient client state.

        Returns:
            ``PaginatedResponse[InstalledEntry]`` instance.
        """
        app_state: AppState = state.app_state
        installations_repo = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_installations_repo,
            "MCP Installations Repository",
        )
        # Drain every installed row before cursor-paginating the
        # response. The bundled catalog is small today (~20-50 entries),
        # but a fixed cap would silently truncate the installed list
        # the moment an operator installs more than _LIST_PAGE_SIZE
        # entries -- the dashboard would then show a partial set with
        # no warning. The drain loop costs one extra round-trip only
        # when the install count crosses the page boundary.
        records_acc: list[McpInstallation] = []
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded drain, not a daemon
        while True:
            batch = await installations_repo.list_items(
                limit=_LIST_PAGE_SIZE,
                offset=offset,
            )
            if not batch:
                break
            records_acc.extend(batch)
            if len(batch) < _LIST_PAGE_SIZE:
                break
            offset += _LIST_PAGE_SIZE
        records = tuple(records_acc)
        entries = tuple(
            InstalledEntry(
                catalog_entry_id=row.catalog_entry_id,
                connection_name=row.connection_name,
                installed_at=row.installed_at.isoformat(),
            )
            for row in records
        )
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

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
        persists the row, and hot-reloads the agent runtime so the
        server's tools go live immediately (best-effort; no restart).
        Re-installing an existing entry is idempotent.

        Returns:
            ``ApiResponse[InstallEntryResponse]`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
            InvalidConnectionAuthError: If the bound connection's type does
                not match the entry's ``required_connection_type`` (422,
                mapped by the central handler from class metadata).
        """
        entry_id = data.catalog_entry_id
        connection_name = data.connection_name

        app_state: AppState = state.app_state
        service = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_catalog_service,
            "MCP Catalog Service",
        )
        installations_repo = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_installations_repo,
            "MCP Installations Repository",
        )
        connection_catalog = app_state.slice(IntegrationsStateSlice).connection_catalog

        await _validate_connection_name_for_install(
            entry_id=entry_id,
            connection_name=connection_name,
            connection_catalog=connection_catalog,
        )

        # ``InvalidConnectionAuthError`` carries its own 422 ``VALIDATION_ERROR``
        # wire contract (the supplied connection is the wrong type for this
        # catalog entry), so it propagates untouched to the central handler
        # instead of being caught and re-mapped.
        result = await service.install(
            entry_id,
            connection_name,
            connection_catalog=connection_catalog,
            installations_repo=installations_repo,
        )

        # NB: we intentionally don't re-log ``MCP_SERVER_INSTALLED``
        # here - the repository's ``save()`` is the canonical audit
        # point and logs the same event with a ``backend`` tag.

        # Hot-reload the runtime so the freshly installed server's tools go
        # live without a manual reload / restart (best-effort).
        await _reload_bridge_best_effort(
            app_state, action="install", catalog_entry_id=entry_id
        )

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

        Returns:
            ``ApiResponse[None]`` instance.
        """
        app_state: AppState = state.app_state
        service = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_catalog_service,
            "MCP Catalog Service",
        )
        installations_repo = require_service(
            app_state.slice(IntegrationsStateSlice).mcp_installations_repo,
            "MCP Installations Repository",
        )
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
        else:
            # Hot-reload the runtime so the removed server's tools stop being
            # offered without a manual reload / restart (best-effort).
            await _reload_bridge_best_effort(
                app_state, action="uninstall", catalog_entry_id=entry_id
            )
        return ApiResponse(data=None)
