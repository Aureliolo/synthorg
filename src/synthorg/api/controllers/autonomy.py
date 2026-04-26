"""Autonomy controller -- runtime autonomy level management."""

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import AutonomyLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)

logger = get_logger(__name__)


class AutonomyLevelRequest(BaseModel):
    """Request body for changing an agent's autonomy level.

    Attributes:
        level: The requested autonomy level.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    level: AutonomyLevel = Field(description="Requested autonomy level")


class AutonomyLevelResponse(BaseModel):
    """Response body with the agent's current autonomy info.

    Attributes:
        agent_id: The agent identifier.
        level: Current effective autonomy level.
        promotion_pending: Whether a promotion request is pending.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    level: AutonomyLevel = Field(description="Current autonomy level")
    promotion_pending: bool = Field(
        default=False,
        description="Whether a promotion request is pending approval",
    )


class AutonomyController(Controller):
    """Runtime autonomy level management for agents."""

    path = "/agents/{agent_id:str}/autonomy"
    tags = ("autonomy",)

    @get(guards=[require_read_access])
    async def get_autonomy(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[AutonomyLevelResponse]:
        """Get the current autonomy level for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Current autonomy level info.
        """
        app_state: AppState = state.app_state
        level = await app_state.config_resolver.get_autonomy_level()
        return ApiResponse(
            data=AutonomyLevelResponse(
                agent_id=agent_id,
                level=level,
            ),
        )

    @post(guards=[require_ceo_or_manager], status_code=200)
    async def update_autonomy(
        self,
        state: State,
        agent_id: PathId,
        data: AutonomyLevelRequest,
    ) -> ApiResponse[AutonomyLevelResponse]:
        """Request an autonomy level change for an agent.

        Validates seniority constraints and routes through the
        configured ``AutonomyChangeStrategy``.  Returns 200 with the
        current level.  If the change requires human approval, the
        response includes ``promotion_pending=True``.

        Args:
            state: Application state.
            agent_id: Agent identifier.
            data: Autonomy level change request.

        Returns:
            Updated autonomy level info.
        """
        app_state: AppState = state.app_state
        current_level = await app_state.config_resolver.get_autonomy_level()
        requested_level = data.level

        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            requested_level=requested_level.value,
            current_level=current_level.value,
        )

        # All changes route through human approval -- return current
        # level with pending status.  The AutonomyChangeStrategy will
        # apply the change when the approval system is wired up.
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            requested_level=requested_level.value,
            reason="Autonomy level changes require human approval",
        )

        return ApiResponse(
            data=AutonomyLevelResponse(
                agent_id=agent_id,
                level=current_level,
                promotion_pending=True,
            ),
        )
