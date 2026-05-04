"""Collaboration scoring controller -- overrides and calibration data."""

from datetime import UTC, datetime, timedelta
from typing import Any

from litestar import Controller, Request, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field

from synthorg.api.auth.models import AuthenticatedUser
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.domain_errors import (
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.collaboration_override_store import (
    CollaborationOverrideStore,  # noqa: TC001
)
from synthorg.hr.performance.models import (
    CollaborationOverride,
    CollaborationScoreResult,
    LlmCalibrationRecord,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_REQUEST_ERROR

logger = get_logger(__name__)


# ── Request/Response DTOs ────────────────────────────────────


class SetOverrideRequest(BaseModel):
    """Request body for setting a collaboration score override.

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


class OverrideResponse(BaseModel):
    """Response body with override details.

    Attributes:
        agent_id: Agent whose score is overridden.
        score: Override score.
        reason: Why the override was applied.
        applied_by: Who applied the override.
        applied_at: When the override was applied.
        expires_at: When the override expires.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    score: float = Field(ge=0.0, le=10.0)
    reason: NotBlankStr
    applied_by: NotBlankStr
    applied_at: AwareDatetime
    expires_at: AwareDatetime | None


class CalibrationSummaryResponse(BaseModel):
    """Response body with LLM calibration data.

    Attributes:
        agent_id: Agent being calibrated.
        record_count: Number of calibration records (computed).
        average_drift: Average score drift (None if no records).
        records: Calibration records.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr
    average_drift: float | None = Field(default=None, ge=0.0, le=10.0)
    records: tuple[LlmCalibrationRecord, ...] = Field(
        default=(),
        description="Calibration records",
    )

    @computed_field(description="Number of calibration records")  # type: ignore[prop-decorator]
    @property
    def record_count(self) -> int:
        """Number of calibration records."""
        return len(self.records)


# ── Controller ───────────────────────────────────────────────


class CollaborationController(Controller):
    """Collaboration scoring overrides and calibration data."""

    path = "/agents/{agent_id:str}/collaboration"
    tags = ("collaboration",)

    @staticmethod
    def _require_override_store(
        state: State,
    ) -> CollaborationOverrideStore:
        """Return the override store or raise 503.

        Args:
            state: Application state.

        Raises:
            ServiceUnavailableError: If the override store is not
                configured.
        """
        app_state: AppState = state.app_state
        tracker = app_state.performance_tracker
        store = tracker.override_store
        if store is None:
            logger.warning(
                API_REQUEST_ERROR,
                path="collaboration/override",
                reason="override_store_not_configured",
            )
            msg = "Override store not configured"
            raise ServiceUnavailableError(msg)
        return store

    @get("/score", guards=[require_read_access])
    async def get_score(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[CollaborationScoreResult]:
        """Get current collaboration score (with override if active).

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Collaboration score result.
        """
        app_state: AppState = state.app_state
        tracker = app_state.performance_tracker
        return ApiResponse(
            data=await tracker.get_collaboration_score(
                NotBlankStr(agent_id),
            ),
        )

    @get("/override", guards=[require_read_access])
    async def get_override(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[OverrideResponse]:
        """Get the active override for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Override details.

        Raises:
            ServiceUnavailableError: If the override store is not configured.
            NotFoundError: If no active override exists.
        """
        store = self._require_override_store(state)
        agent_nb = NotBlankStr(agent_id)
        override = store.get_active_override(agent_nb)
        if override is None:
            logger.warning(
                API_REQUEST_ERROR,
                path="collaboration/override",
                reason="override_not_found",
                agent_id=agent_id,
            )
            msg = "No active override for the specified agent"
            raise NotFoundError(msg)

        return ApiResponse(
            data=OverrideResponse(
                agent_id=override.agent_id,
                score=override.score,
                reason=override.reason,
                applied_by=override.applied_by,
                applied_at=override.applied_at,
                expires_at=override.expires_at,
            ),
        )

    @post(
        "/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("collaboration.override", key="user"),
        ],
        status_code=200,
    )
    async def set_override(
        self,
        state: State,
        agent_id: PathId,
        data: SetOverrideRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[OverrideResponse]:
        """Set a collaboration score override for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.
            data: Override request body.
            request: The incoming HTTP request.

        Returns:
            The created override.

        Raises:
            ServiceUnavailableError: If the override store is not
                configured or user identity cannot be determined.
        """
        store = self._require_override_store(state)

        now = datetime.now(UTC)
        expires_at = (
            now + timedelta(days=data.expires_in_days)
            if data.expires_in_days is not None
            else None
        )

        # Extract user identity from the authenticated request.
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.error(
                API_REQUEST_ERROR,
                path="collaboration/override",
                reason="user_identity_extraction_failed",
                agent_id=agent_id,
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        override = CollaborationOverride(
            agent_id=NotBlankStr(agent_id),
            score=data.score,
            reason=data.reason,
            applied_by=NotBlankStr(str(auth_user.user_id)),
            applied_at=now,
            expires_at=expires_at,
        )
        store.set_override(override)

        return ApiResponse(
            data=OverrideResponse(
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
            per_op_rate_limit_from_policy("collaboration.override", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def clear_override(
        self,
        state: State,
        agent_id: PathId,
    ) -> None:
        """Clear the active override for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Raises:
            ServiceUnavailableError: If the override store is not configured.
            NotFoundError: If no override exists to clear.
        """
        store = self._require_override_store(state)
        agent_nb = NotBlankStr(agent_id)
        removed = store.clear_override(agent_nb)
        if not removed:
            logger.warning(
                API_REQUEST_ERROR,
                path="collaboration/override",
                reason="override_not_found",
                agent_id=agent_id,
            )
            msg = "No override to clear for the specified agent"
            raise NotFoundError(msg)

    @get("/calibration", guards=[require_read_access])
    async def get_calibration(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[CalibrationSummaryResponse]:
        """Get LLM calibration records and drift summary.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Calibration summary with records and drift.
        """
        app_state: AppState = state.app_state
        tracker = app_state.performance_tracker
        agent_nb = NotBlankStr(agent_id)

        records: tuple[LlmCalibrationRecord, ...] = ()
        average_drift: float | None = None

        if tracker.sampler is not None:
            records = tracker.sampler.get_calibration_records(
                agent_id=agent_nb,
            )
            average_drift = tracker.sampler.get_drift_summary(agent_nb)

        return ApiResponse(
            data=CalibrationSummaryResponse(
                agent_id=agent_nb,
                average_drift=average_drift,
                records=records,
            ),
        )
