# module-kind: controller
"""Ontology drift-detection controller."""

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg.api.controllers.ontology._shared import _DEFAULT_LIMIT
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_ontology import DriftReportResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.observability.events.ontology import (
    ONTOLOGY_DRIFT_CHECK_COMPLETED,
    ONTOLOGY_DRIFT_CHECK_STARTED,
)
from synthorg.ontology.models import DriftReport
from synthorg.ontology.state import OntologyStateSlice

logger = get_logger(__name__)


def _drift_report_to_response(
    report: DriftReport,
) -> DriftReportResponse:
    """Convert a DriftReport to a DriftReportResponse.

    Returns:
        ``DriftReportResponse`` instance.
    """
    from synthorg.api.dto_ontology import DriftAgentResponse  # noqa: PLC0415

    return DriftReportResponse(
        entity_name=report.entity_name,
        divergence_score=report.divergence_score,
        divergent_agents=tuple(
            DriftAgentResponse(
                agent_id=a.agent_id,
                divergence_score=a.divergence_score,
                details=a.details,
            )
            for a in report.divergent_agents
        ),
        canonical_version=report.canonical_version,
        recommendation=report.recommendation,
    )


class OntologyDriftController(Controller):
    """Drift detection for ontology entity definitions."""

    path = "/ontology"
    tags = ("ontology",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/drift")
    async def list_drift_reports(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[DriftReportResponse]:
        """Get latest drift reports for all entities.

        Returns:
            ``PaginatedResponse[DriftReportResponse]`` instance.
        """
        app_state: AppState = state.app_state
        store = app_state.slice(OntologyStateSlice).drift_report_store
        if store is None:
            _, meta = paginate_cursor(
                (),
                limit=limit,
                cursor=cursor,
                secret=cursor_secret_of(app_state),
            )
            return PaginatedResponse(data=(), pagination=meta)

        # Decode the cursor offset at the controller so the fetch
        # window covers every row ``paginate_cursor`` will slice:
        # it indexes ``items[offset : offset + limit]``, so a window
        # of ``offset + limit + 1`` keeps later pages complete while
        # the trailing extra row lets ``has_more`` detect a next page
        # (a bare ``limit + 1`` window truncated cursor pages to
        # slices of the first window).
        offset = (
            0
            if cursor is None
            else decode_cursor(cursor, secret=cursor_secret_of(app_state))
        )
        reports = await store.get_all_latest(limit=offset + limit + 1)
        responses = tuple(_drift_report_to_response(r) for r in reports)
        page, meta = paginate_cursor(
            responses,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/drift/{entity_name:str}")
    async def get_drift_report(
        self,
        state: State,
        entity_name: PathName,
    ) -> ApiResponse[tuple[DriftReportResponse, ...]]:
        """Get drift reports for a specific entity.

        Returns:
            Result matching the declared return annotation.
        """
        app_state: AppState = state.app_state
        store = app_state.slice(OntologyStateSlice).drift_report_store
        if store is None:
            return ApiResponse(data=())

        reports = await store.get_latest(entity_name)
        responses = tuple(_drift_report_to_response(r) for r in reports)
        return ApiResponse(data=responses)

    @post(
        "/drift/check",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.drift_check", key="user"),
        ],
    )
    async def trigger_drift_check(
        self,
        state: State,
    ) -> ApiResponse[dict[str, str]]:
        """Trigger on-demand drift check for all entities.

        Returns:
            ``ApiResponse[dict[str, str]]`` instance.
        """
        app_state: AppState = state.app_state
        drift_service = app_state.slice(OntologyStateSlice).drift_detection_service
        if drift_service is None:
            logger.warning(
                API_REQUEST_ERROR,
                reason="drift_service_unavailable",
            )
            return ApiResponse(
                data={"status": "drift_service_not_configured"},
            )

        # Agent discovery is handled by the engine -- trigger uses
        # empty tuple to signal "check all entities, no agent sample".
        logger.info(ONTOLOGY_DRIFT_CHECK_STARTED, source="api")
        await drift_service.check_all(agent_ids=())
        logger.info(ONTOLOGY_DRIFT_CHECK_COMPLETED, source="api")
        return ApiResponse(data={"status": "drift_check_completed"})
