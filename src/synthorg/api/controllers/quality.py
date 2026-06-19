"""Quality scoring controller -- overrides for task quality scores."""

from datetime import UTC, datetime, timedelta

from litestar import Controller, Request, delete, get, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import QualityOverride
from synthorg.hr.performance.quality_override_store import (
    QualityOverrideStore,
)
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_REQUEST_ERROR

logger = get_logger(__name__)


# -- Request/Response DTOs ---------------------------------------------------


class SetQualityOverrideRequest(BaseModel):
    """Request body for setting a quality score override.

    Attributes:
        score: Override score (0.0-10.0).
        reason: Why the override is being applied.
        expires_in_days: Optional expiration in days (None = indefinite).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    score: float = Field(ge=0.0, le=10.0, description="Override score")
    reason: NotBlankStr = Field(
        max_length=4096,
        description="Reason for the override",
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Expiration in days (None = indefinite)",
    )


class QualityOverrideResponse(BaseModel):
    """Response body with quality override details.

    Attributes:
        agent_id: Agent whose quality score is overridden.
        score: Override score.
        reason: Why the override was applied.
        applied_by: Who applied the override.
        applied_at: When the override was applied.
        expires_at: When the override expires.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(
        description="Agent whose quality score is overridden",
    )
    score: float = Field(
        ge=0.0,
        le=10.0,
        description="Override score",
    )
    reason: NotBlankStr = Field(description="Why the override was applied")
    applied_by: NotBlankStr = Field(description="Who applied the override")
    applied_at: AwareDatetime = Field(
        description="When the override was applied",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="When the override expires",
    )


# -- Controller --------------------------------------------------------------


class QualityController(Controller):
    """Quality scoring overrides for task quality scores."""

    path = "/agents/{agent_id:str}/quality"
    tags = ("quality",)

    @staticmethod
    def _require_quality_override_store(
        state: State,
    ) -> QualityOverrideStore:
        """Return the quality override store or raise 503.

        Args:
            state: Application state.

        Raises:
            ServiceUnavailableError: If the quality override store is
                not configured.

        Returns:
            ``QualityOverrideStore`` instance.
        """
        app_state: AppState = state.app_state
        tracker = require_service(
            app_state.slice(HrStateSlice).performance_tracker, "Performance Tracker"
        )
        store = tracker.quality_override_store
        if store is None:
            logger.warning(
                API_REQUEST_ERROR,
                path="quality/override",
                reason="quality_override_store_not_configured",
            )
            msg = "Quality override store not configured"
            raise ServiceUnavailableError(msg)
        return store

    @get("/override", guards=[require_read_access])
    async def get_override(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[QualityOverrideResponse]:
        """Get the active quality override for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Override details.

        Raises:
            ServiceUnavailableError: If the override store is not configured.
            NotFoundError: If no active override exists.
        """
        store = self._require_quality_override_store(state)
        agent_nb = NotBlankStr(agent_id)
        override = store.get_active_override(agent_nb)
        if override is None:
            logger.warning(
                API_REQUEST_ERROR,
                path="quality/override",
                reason="override_not_found",
                agent_id=agent_id,
            )
            msg = "No active quality override for the specified agent"
            raise NotFoundError(msg)

        return ApiResponse(
            data=QualityOverrideResponse(
                agent_id=override.agent_id,
                score=override.score,
                reason=override.reason,
                applied_by=override.applied_by,
                applied_at=override.applied_at,
                expires_at=override.expires_at,
            ),
        )

    # Guards intentionally use require_ceo_or_manager (not
    # require_write_access) -- PAIR_PROGRAMMER is excluded because
    # quality overrides affect evaluation scores and should only be
    # set by management roles.
    @post(
        "/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("quality.override", key="user"),
        ],
        status_code=200,
    )
    async def set_override(
        self,
        state: State,
        agent_id: PathId,
        data: SetQualityOverrideRequest,
        request: Request[object, object, State],
    ) -> ApiResponse[QualityOverrideResponse]:
        """Set a quality score override for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.
            data: Override request body.
            request: The incoming HTTP request.

        Returns:
            The created override.

        Raises:
            ServiceUnavailableError: If the override store is not
                configured.
            UnauthorizedError: If the authenticated user identity
                cannot be resolved from the request scope.
            ConflictError: Raised on the corresponding failure path.
        """
        store = self._require_quality_override_store(state)

        now = datetime.now(UTC)
        expires_at = (
            now + timedelta(days=data.expires_in_days)
            if data.expires_in_days is not None
            else None
        )

        # Extract user identity from the authenticated request.
        auth_user = require_authenticated_user(request)

        override = QualityOverride(
            agent_id=NotBlankStr(agent_id),
            score=data.score,
            reason=data.reason,
            applied_by=NotBlankStr(str(auth_user.user_id)),
            applied_at=now,
            expires_at=expires_at,
        )
        try:
            store.set_override(override)
        except ValueError as exc:
            logger.warning(
                API_REQUEST_ERROR,
                path="quality/override",
                reason="capacity_reached",
                agent_id=agent_id,
            )
            raise ConflictError(str(exc)) from exc

        return ApiResponse(
            data=QualityOverrideResponse(
                agent_id=override.agent_id,
                score=override.score,
                reason=override.reason,
                applied_by=override.applied_by,
                applied_at=override.applied_at,
                expires_at=override.expires_at,
            ),
        )

    @delete(
        "/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("quality.delete_override", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def clear_override(
        self,
        state: State,
        agent_id: PathId,
    ) -> None:
        """Clear the active quality override for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Raises:
            ServiceUnavailableError: If the override store is not configured.
            NotFoundError: If no override exists to clear.
        """
        store = self._require_quality_override_store(state)
        agent_nb = NotBlankStr(agent_id)
        removed = store.clear_override(agent_nb)
        if not removed:
            logger.warning(
                API_REQUEST_ERROR,
                path="quality/override",
                reason="override_not_found",
                agent_id=agent_id,
            )
            msg = "No quality override to clear for the specified agent"
            raise NotFoundError(msg)
