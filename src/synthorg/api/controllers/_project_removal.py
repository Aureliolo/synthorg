# module-kind: code
"""Remove a project, with everything a deletion owes done in the right order.

Read the project first: after the row is gone nothing can say what the
surviving records are naming. Supersede its live plans and cancel its open
tasks BEFORE the row goes, or a plan is left pointing at a project that does
not exist. Take the workspace its agents wrote into, which is ours by
construction and nothing else will collect. Then the tombstone, then the event
the board listens on.

The bulk form sits beside the single one because it IS the single one repeated:
what differs is that a refusal is collected against its row instead of ending
the request.
"""

from litestar.channels import ChannelsPlugin

from synthorg.api.channels import CHANNEL_PROJECTS, publish_ws_event_with_plugin
from synthorg.api.controllers._bulk_delete import (
    BulkDeleteResult,
    resolve_bulk_delete_budget,
    run_bulk_delete,
)
from synthorg.api.controllers._deletion_record import record_deletion
from synthorg.api.controllers._project_cascade import cascade_supersede_children
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.project_service import ProjectService
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.core.deleted_entity import DeletedEntityKind
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


async def remove_project(
    app_state: AppState,
    project_id: str,
    *,
    requested_by: str,
    channels_plugin: ChannelsPlugin | None,
) -> None:
    """Delete *project_id* and everything that hangs off it.

    Takes the resolved plugin rather than a request, because this is not only
    a route: the MCP project tool deletes through the same path, and it has no
    request to resolve one from. It builds its own service for the same
    reason: a caller below the api layer is entitled to this cascade but not
    to the endpoint-audit service that performs the row write.

    Args:
        app_state: Application state.
        project_id: The project to remove.
        requested_by: The person who asked.
        channels_plugin: Where the board's event goes, or ``None`` to drop it.

    Raises:
        NotFoundError: Project with ``project_id`` does not exist, or the row
            disappeared between the read and the delete.
    """
    await _remove_one(
        app_state,
        _service(app_state),
        project_id,
        requested_by=requested_by,
        channels_plugin=channels_plugin,
    )


def _service(app_state: AppState) -> ProjectService:
    """Build the project service over this deployment's persisted projects.

    Returns:
        A service bound to the same repository the route's own reads use.
    """
    return ProjectService(repo=persistence_of(app_state).projects)


async def _remove_one(
    app_state: AppState,
    service: ProjectService,
    project_id: str,
    *,
    requested_by: str,
    channels_plugin: ChannelsPlugin | None,
) -> None:
    """Remove one project through an already-built *service*.

    Args:
        app_state: Application state.
        service: The service the caller built once for the whole selection.
        project_id: The project to remove.
        requested_by: The person who asked.
        channels_plugin: Where the board's event goes, or ``None`` to drop it.

    Raises:
        NotFoundError: Project with ``project_id`` does not exist, or the row
            disappeared between the read and the delete.
    """
    project = require_resource_or_404(
        await service.get(project_id),
        resource_type="Project",
        identifier=project_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="delete",
        extra_log_kwargs={"project_id": project_id},
    )
    await cascade_supersede_children(
        app_state,
        project_id,
        requested_by=requested_by,
    )
    deleted = await service.delete(project_id)
    if not deleted:
        # Race: row disappeared between get() and delete(). Log as a warning
        # so concurrent destructive operations stay in the audit trail.
        logger.warning(
            API_RESOURCE_NOT_FOUND,
            resource="project",
            project_id=project_id,
            operation="delete",
            note="concurrent_delete",
        )
        msg = f"Project {project_id!r} not found"
        raise NotFoundError(msg)
    await record_deletion(
        persistence_of(app_state),
        kind=DeletedEntityKind.PROJECT,
        entity_id=project_id,
        display_name=project.name,
        deleted_by=requested_by,
    )
    publish_ws_event_with_plugin(
        channels_plugin,
        WsEventType.PROJECT_DELETED,
        CHANNEL_PROJECTS,
        {"project_id": project_id, "name": project.name},
        clock=app_state.clock,
    )


async def remove_projects(
    app_state: AppState,
    ids: tuple[NotBlankStr, ...],
    *,
    requested_by: str,
    channels_plugin: ChannelsPlugin | None,
) -> BulkDeleteResult:
    """Delete every project in *ids*, collecting the ones that refuse.

    Args:
        app_state: Application state.
        ids: The projects the operator selected.
        requested_by: The person who asked.
        channels_plugin: Where each removal's event goes.

    Returns:
        What was removed and what remains.
    """
    service = _service(app_state)
    return await run_bulk_delete(
        ids,
        lambda project_id: _remove_one(
            app_state,
            service,
            project_id,
            requested_by=requested_by,
            channels_plugin=channels_plugin,
        ),
        entity="project",
        clock=app_state.clock,
        budget_seconds=await resolve_bulk_delete_budget(app_state),
    )


__all__ = ["remove_project", "remove_projects"]
