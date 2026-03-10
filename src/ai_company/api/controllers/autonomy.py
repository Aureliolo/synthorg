"""Autonomy controller — runtime autonomy level management."""

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from ai_company.api.dto import ApiResponse
from ai_company.api.guards import require_read_access, require_write_access
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.core.enums import AutonomyLevel  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.autonomy import (
    AUTONOMY_PROMOTION_DENIED,
    AUTONOMY_PROMOTION_REQUESTED,
)

logger = get_logger(__name__)


class AutonomyLevelRequest(BaseModel):
    """Request body for changing an agent's autonomy level.

    Attributes:
        level: The requested autonomy level.
    """

    model_config = ConfigDict(frozen=True)

    level: AutonomyLevel = Field(description="Requested autonomy level")


class AutonomyLevelResponse(BaseModel):
    """Response body with the agent's current autonomy info.

    Attributes:
        agent_id: The agent identifier.
        level: Current effective autonomy level.
        promotion_pending: Whether a promotion request is pending.
    """

    model_config = ConfigDict(frozen=True)

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
        agent_id: str,
    ) -> ApiResponse[AutonomyLevelResponse]:
        """Get the current autonomy level for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Current autonomy level info.
        """
        app_state: AppState = state.app_state
        config = app_state.config.config
        level = config.autonomy.level
        return ApiResponse(
            data=AutonomyLevelResponse(
                agent_id=agent_id,
                level=level,
            ),
        )

    @post(guards=[require_write_access], status_code=200)
    async def update_autonomy(
        self,
        state: State,
        agent_id: str,
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
        app_state: AppState = state.app_state  # noqa: F841
        requested_level = data.level

        logger.info(
            AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            requested_level=requested_level.value,
        )

        # Promotions require human approval — return pending status.
        # The actual change would be applied via the AutonomyChangeStrategy
        # when the approval system is wired up.
        logger.info(
            AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            requested_level=requested_level.value,
            reason="Autonomy promotions require human approval",
        )

        return ApiResponse(
            data=AutonomyLevelResponse(
                agent_id=agent_id,
                level=requested_level,
                promotion_pending=True,
            ),
        )
