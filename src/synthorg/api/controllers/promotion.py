# module-kind: controller
"""Promotion controller -- REST endpoints for agent seniority changes.

Exposes promotion/demotion evaluation, single-agent application (with
the human-approval gate honoured), per-agent history, and a manual
cycle trigger. Mutating endpoints are CEO/Manager-guarded and
rate-limited; the service is wired by ``wire_promotion`` at startup, so
a request before wiring honestly 503s via ``promotion_service_of``.
"""

from typing import Annotated

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.dto import ApiResponse
from synthorg.api.dto_promotion import (
    PromotionApplyResultDTO,
    PromotionEvaluationDTO,
    PromotionRecordDTO,
    PromotionRequestDTO,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.state import promotion_service_of
from synthorg.observability import get_logger
from synthorg.observability.events.promotion import (
    PROMOTION_CYCLE_RAN,
    PROMOTION_REQUESTED,
)

logger = get_logger(__name__)


class PromotionController(Controller):
    """Agent promotion / demotion endpoints."""

    path = "/promotion"
    tags = ("promotion",)

    @get("/{agent_id:str}/evaluate", guards=[require_read_access])
    async def evaluate(
        self,
        state: State,
        agent_id: PathId,
        direction: Annotated[
            PromotionDirection,
            QueryParameter(description="Whether to evaluate a promotion or demotion."),
        ] = PromotionDirection.PROMOTION,
    ) -> ApiResponse[PromotionEvaluationDTO]:
        """Evaluate an agent for promotion or demotion.

        Args:
            state: Application state.
            agent_id: Agent to evaluate.
            direction: Whether to evaluate a promotion or a demotion.

        Returns:
            The evaluation outcome.
        """
        app_state: AppState = state.app_state
        service = promotion_service_of(app_state)
        aid = NotBlankStr(agent_id)
        if direction == PromotionDirection.DEMOTION:
            evaluation = await service.evaluate_demotion(aid)
        else:
            evaluation = await service.evaluate_promotion(aid)
        return ApiResponse(data=PromotionEvaluationDTO.from_domain(evaluation))

    @get("/{agent_id:str}/history", guards=[require_read_access])
    async def history(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[tuple[PromotionRecordDTO, ...]]:
        """List an agent's promotion/demotion history.

        Args:
            state: Application state.
            agent_id: Agent whose history to read.

        Returns:
            The agent's promotion records, newest last.
        """
        app_state: AppState = state.app_state
        service = promotion_service_of(app_state)
        records = service.get_promotion_history(NotBlankStr(agent_id))
        return ApiResponse(
            data=tuple(PromotionRecordDTO.from_domain(r) for r in records),
        )

    @post(
        "/{agent_id:str}/apply",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("promotion.apply", key="user"),
        ],
    )
    async def apply(
        self,
        state: State,
        agent_id: PathId,
        direction: Annotated[
            PromotionDirection,
            QueryParameter(description="Whether to apply a promotion or demotion."),
        ] = PromotionDirection.PROMOTION,
    ) -> ApiResponse[PromotionApplyResultDTO]:
        """Evaluate, request, and apply a seniority change for one agent.

        Auto-approved changes are applied immediately; changes requiring
        human approval create an approval item and return a pending
        request without applying.

        Args:
            state: Application state.
            agent_id: Agent to promote or demote.
            direction: Whether to promote or demote.

        Returns:
            The request plus the applied record when auto-approved.
        """
        app_state: AppState = state.app_state
        service = promotion_service_of(app_state)
        aid = NotBlankStr(agent_id)
        if direction == PromotionDirection.DEMOTION:
            evaluation = await service.evaluate_demotion(aid)
        else:
            evaluation = await service.evaluate_promotion(aid)
        request = await service.request_promotion(aid, evaluation)
        logger.info(
            PROMOTION_REQUESTED,
            agent_id=agent_id,
            direction=direction.value,
            status=request.status.value,
        )
        applied = None
        if request.status == ApprovalStatus.APPROVED:
            applied = await service.apply_promotion(request)
        return ApiResponse(
            data=PromotionApplyResultDTO(
                request=PromotionRequestDTO.from_domain(request),
                applied=(
                    PromotionRecordDTO.from_domain(applied)
                    if applied is not None
                    else None
                ),
            ),
        )

    @post(
        "/cycle",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("promotion.trigger_cycle", key="user"),
        ],
    )
    async def trigger_cycle(
        self,
        state: State,
    ) -> ApiResponse[tuple[PromotionRecordDTO, ...]]:
        """Manually run one promotion cycle over all active agents.

        Args:
            state: Application state.

        Returns:
            The records applied during the cycle.
        """
        app_state: AppState = state.app_state
        service = promotion_service_of(app_state)
        applied = await service.run_cycle()
        logger.info(PROMOTION_CYCLE_RAN, applied=len(applied), trigger="manual")
        return ApiResponse(
            data=tuple(PromotionRecordDTO.from_domain(r) for r in applied),
        )
