# module-kind: controller
"""Ontology admin controller -- derivation and org-memory sync."""

from litestar import Controller, post
from litestar.datastructures import State

from synthorg.api.controllers.ontology._shared import _ontology_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.observability.events.ontology import ONTOLOGY_ADMIN_SYNC_COMPLETED
from synthorg.ontology.state import OntologyStateSlice

logger = get_logger(__name__)


class OntologyAdminController(Controller):
    """Admin operations for the ontology subsystem."""

    path = "/ontology"
    tags = ("ontology",)
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/admin/derive",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.admin_derive", key="user"),
        ],
    )
    async def admin_derive(
        self,
        state: State,
    ) -> ApiResponse[dict[str, int]]:
        """Re-run auto-derivation from decorated models.

        Returns:
            ``ApiResponse[dict[str, int]]`` instance.
        """
        app_state: AppState = state.app_state
        count = await _ontology_service(app_state).bootstrap()
        return ApiResponse(data={"derived_count": count})

    @post(
        "/admin/sync-org-memory",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy(
                "ontology.admin_sync_org_memory",
                key="user",
            ),
        ],
    )
    async def admin_sync_org_memory(
        self,
        state: State,
    ) -> ApiResponse[dict[str, int | str]]:
        """Force re-sync all entity definitions to OrgMemory.

        Returns:
            ``ApiResponse[dict[str, int | str]]`` instance.

        Raises:
            ServiceUnavailableError: When the ontology sync service is
                not wired (503 rather than a 200 success-shaped body).
        """
        app_state: AppState = state.app_state
        sync_service = app_state.slice(OntologyStateSlice).sync_service
        if sync_service is None:
            logger.warning(
                API_REQUEST_ERROR,
                reason="sync_service_unavailable",
                error_type=ServiceUnavailableError.__name__,
            )
            msg = "Ontology sync service is not configured"
            raise ServiceUnavailableError(msg)

        count = await sync_service.sync_all()
        logger.info(
            ONTOLOGY_ADMIN_SYNC_COMPLETED,
            published_count=count,
        )
        return ApiResponse(
            data={"status": "sync_completed", "published_count": count},
        )
