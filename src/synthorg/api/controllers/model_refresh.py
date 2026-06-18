# module-kind: controller
"""Model-refresh + upgrade-recommendation review controllers.

Endpoints under ``/providers/model-refresh``:

* ``GET /recommendations`` -- list upgrade recommendations (filter by status).
* ``POST /recommendations/{id}/approve`` -- approve + reassign pinned agents.
* ``POST /recommendations/{id}/reject`` -- reject (no reassignment).
* ``POST /refresh`` -- run one reconcile+recommend cycle on demand.
* ``GET /status`` -- current refresh mode / cadence / auto-apply flag.
"""

from typing import Annotated
from uuid import UUID

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.api_core_state import org_mutation_service_of
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_model_refresh import (
    RefreshCycleReportDTO,
    RefreshStatusDTO,
    UpgradeRecommendationDTO,
)
from synthorg.api.guards import require_ceo_or_manager, require_write_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.services.upgrade_recommendation_service import (
    UpgradeRecommendationService,
)
from synthorg.api.state import AppState
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_MODEL_REFRESH_CYCLE_FAILED
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.refresh_config import (
    RefreshMode,
    load_model_refresh_config,
)
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


def _recommendation_service(state: State) -> UpgradeRecommendationService:
    """Build the recommendation service or raise 503.

    Returns:
        An ``UpgradeRecommendationService`` over the wired repo.

    Raises:
        ServiceUnavailableError: When the recommendation store is unwired.
    """
    app_state: AppState = state.app_state
    repo = app_state.slice(ModelRefreshStateSlice).recommendation_repo
    if repo is None:
        logger.warning(PROVIDER_MODEL_REFRESH_CYCLE_FAILED, note="repo_unavailable")
        msg = "Model-refresh recommendation store not configured"
        raise ServiceUnavailableError(msg)
    return UpgradeRecommendationService(
        repo=repo,
        org_mutations=org_mutation_service_of(app_state),
    )


class ModelRefreshController(Controller):
    """Upgrade-recommendation review + manual refresh endpoints."""

    path = "/providers/model-refresh"
    guards = (require_write_access,)
    tags = ("providers", "model-refresh")

    @get("/recommendations")
    async def list_recommendations(
        self,
        state: State,
        status: Annotated[RecommendationStatus | None, QueryParameter()] = None,
    ) -> ApiResponse[tuple[UpgradeRecommendationDTO, ...]]:
        """List upgrade recommendations, optionally filtered by status.

        Returns:
            The matching recommendations, newest-first.
        """
        service = _recommendation_service(state)
        rows = await service.list_recommendations(status=status)
        return ApiResponse(
            data=tuple(UpgradeRecommendationDTO.from_entity(r) for r in rows),
        )

    @post(
        "/recommendations/{rec_id:str}/approve",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.model_refresh_decide", key="user"),
        ],
    )
    async def approve_recommendation(
        self,
        rec_id: PathId,
        state: State,
    ) -> ApiResponse[UpgradeRecommendationDTO]:
        """Approve a pending recommendation and reassign pinned agents.

        The deciding operator is taken from the authenticated session
        (never the request body), so the audit trail cannot be forged.

        Returns:
            The approved recommendation.
        """
        service = _recommendation_service(state)
        updated = await service.approve(UUID(rec_id), decided_by=resolve_decided_by())
        return ApiResponse(data=UpgradeRecommendationDTO.from_entity(updated))

    @post(
        "/recommendations/{rec_id:str}/reject",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.model_refresh_decide", key="user"),
        ],
    )
    async def reject_recommendation(
        self,
        rec_id: PathId,
        state: State,
    ) -> ApiResponse[UpgradeRecommendationDTO]:
        """Reject a pending recommendation (no reassignment).

        The deciding operator is taken from the authenticated session
        (never the request body), so the audit trail cannot be forged.

        Returns:
            The rejected recommendation.
        """
        service = _recommendation_service(state)
        updated = await service.reject(UUID(rec_id), decided_by=resolve_decided_by())
        return ApiResponse(data=UpgradeRecommendationDTO.from_entity(updated))

    @post(
        "/refresh",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.model_refresh_trigger", key="user"
            ),
        ],
    )
    async def trigger_refresh(self, state: State) -> ApiResponse[RefreshCycleReportDTO]:
        """Run one reconcile+recommend cycle on demand.

        Restricted to CEO / Manager: it drives live discovery against
        every configured provider's endpoint. Available regardless of
        cadence mode so ``manual_only`` operators can refresh on demand.
        Human approval still gates apply (no auto-apply on a manual
        trigger).

        Returns:
            The aggregate cycle report.

        Raises:
            ServiceUnavailableError: When the refresh service is unwired.
        """
        app_state: AppState = state.app_state
        service = app_state.slice(ModelRefreshStateSlice).service
        if service is None:
            logger.warning(
                PROVIDER_MODEL_REFRESH_CYCLE_FAILED, note="service_unavailable"
            )
            msg = "Model-refresh service not configured"
            raise ServiceUnavailableError(msg)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        return ApiResponse(data=RefreshCycleReportDTO.from_report(report))

    @get("/status")
    async def get_status(self, state: State) -> ApiResponse[RefreshStatusDTO]:
        """Return the current model-refresh configuration.

        Returns:
            The refresh mode, cadence, and auto-apply flag.
        """
        app_state: AppState = state.app_state
        config = await load_model_refresh_config(config_resolver_of(app_state))
        return ApiResponse(data=RefreshStatusDTO.from_config(config))
