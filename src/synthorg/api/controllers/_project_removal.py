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

from litestar import Request
from litestar.datastructures import State

from synthorg.api.channels import CHANNEL_PROJECTS, publish_ws_event
from synthorg.api.controllers._bulk_delete import BulkDeleteResult, run_bulk_delete
from synthorg.api.controllers._deletion_record import record_deletion
from synthorg.api.controllers._project_cascade import cascade_supersede_children
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.project_service import ProjectService
from synthorg.api.ws_models import WsEventType
from synthorg.core.deleted_entity import DeletedEntityKind
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


async def remove_project(
    request: Request[object, object, State],
    state: State,
    service: ProjectService,
    project_id: str,
    *,
    requested_by: str,
) -> None:
    """Delete *project_id* and everything that hangs off it.

    Args:
        request: The incoming request, for the WebSocket publish.
        state: Application state.
        service: The project service the route already built.
        project_id: The project to remove.
        requested_by: The person who asked.

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
        state.app_state,
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
        persistence_of(state.app_state),
        kind=DeletedEntityKind.PROJECT,
        entity_id=project_id,
        display_name=project.name,
        deleted_by=requested_by,
    )
    publish_ws_event(
        request,
        WsEventType.PROJECT_DELETED,
        CHANNEL_PROJECTS,
        {"project_id": project_id, "name": project.name},
    )


async def remove_projects(
    request: Request[object, object, State],
    state: State,
    service: ProjectService,
    ids: tuple[NotBlankStr, ...],
    *,
    requested_by: str,
) -> BulkDeleteResult:
    """Delete every project in *ids*, collecting the ones that refuse.

    Args:
        request: The incoming request, for the WebSocket publishes.
        state: Application state.
        service: The project service the route already built.
        ids: The projects the operator selected.
        requested_by: The person who asked.

    Returns:
        What was removed and what remains.
    """
    return await run_bulk_delete(
        ids,
        lambda project_id: remove_project(
            request, state, service, project_id, requested_by=requested_by
        ),
        entity="project",
    )


__all__ = ["remove_project", "remove_projects"]
