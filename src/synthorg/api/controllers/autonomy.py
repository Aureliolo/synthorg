"""Autonomy controller -- runtime autonomy level management."""

from typing import Final, Self

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.domain_errors import ForbiddenError, NotFoundError
from synthorg.core.enums import AutonomyLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import AutonomyUpdate
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

# Minimum non-whitespace characters in an autonomy-change reason.
# Mirrors ``AutonomyUpdate`` so the request body is self-validating
# (rejected at the API boundary, not late in registry construction).
_MIN_REASON_LENGTH: Final[int] = 3
_MAX_REASON_LENGTH: Final[int] = 2048


class AutonomyLevelRequest(BaseModel):
    """Request body for changing an agent's autonomy level.

    Attributes:
        level: The requested autonomy level.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: AutonomyLevel = Field(description="Requested autonomy level")
    reason: NotBlankStr = Field(
        max_length=_MAX_REASON_LENGTH,
        description=(
            "Justification for the change, recorded on the approval"
            " item so the audit trail explains why. At least 3"
            " non-whitespace characters after stripping."
        ),
    )

    @model_validator(mode="after")
    def _validate_reason_length(self) -> Self:
        """Reject reasons below the non-whitespace minimum.

        Mirrors ``AutonomyUpdate`` so an under-length reason is a 4xx
        at the request boundary rather than a late failure in registry
        construction.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if len(self.reason.strip()) < _MIN_REASON_LENGTH:
            msg = (
                f"reason must contain at least {_MIN_REASON_LENGTH} "
                f"non-whitespace characters"
            )
            raise ValueError(msg)
        return self


class AutonomyLevelResponse(BaseModel):
    """Response body with the agent's current autonomy info.

    Attributes:
        agent_id: The agent identifier.
        level: Current effective autonomy level.
        promotion_pending: Whether a promotion request is pending.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        level = await config_resolver_of(app_state).get_autonomy_level()
        return ApiResponse(
            data=AutonomyLevelResponse(
                agent_id=agent_id,
                level=level,
            ),
        )

    @post(
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("agents.autonomy_change", key="user"),
        ],
        status_code=200,
    )
    async def update_autonomy(
        self,
        state: State,
        agent_id: PathId,
        data: AutonomyLevelRequest,
    ) -> ApiResponse[AutonomyLevelResponse]:
        """Request an autonomy level change for an agent.

        Enforces the D6 seniority constraint, consults the configured
        :class:`AutonomyChangeStrategy` (wired at boot; default
        ``HUMAN_ONLY``), and enqueues a real approval item -- the
        approval queue is the apply driver per the Security design
        spec. With ``HUMAN_ONLY`` every request pends for human
        review; the strategy's verdict is carried for audit so an
        auto-grant strategy is observable.

        Args:
            state: Application state.
            agent_id: Agent identifier.
            data: Autonomy level change request.

        Returns:
            Updated autonomy level info.

        Raises:
            NotFoundError: The agent is not registered (404).
            ForbiddenError: The agent's seniority cannot hold the
                requested autonomy level (D6) (403).
        """
        app_state: AppState = state.app_state
        agent_key = NotBlankStr(str(agent_id))
        requested_level = data.level

        registry = require_service(
            app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
        )
        identity = await registry.get(agent_key)
        if identity is None:
            logger.warning(
                SECURITY_AUTONOMY_PROMOTION_DENIED,
                agent_id=agent_key,
                requested_level=requested_level.value,
                reason="agent_not_registered",
            )
            msg = "Agent not found"
            raise NotFoundError(msg)

        resolver = AutonomyResolver(
            registry=ActionTypeRegistry(),
            config=app_state.config.config.autonomy,
        )
        try:
            resolver.validate_seniority(identity.level, requested_level)
        except ValueError as exc:
            # Detail already logged by the resolver
            # (AUTONOMY_SENIORITY_VIOLATION); return a generic 403 so
            # the seniority policy is not leaked verbatim.
            forbidden_msg = (
                "Agent seniority does not permit the requested autonomy level"
            )
            raise ForbiddenError(forbidden_msg) from exc

        # Consult the boot-wired strategy. HUMAN_ONLY always returns
        # False (pending); an opt-in auto-grant strategy returns True.
        strategy = require_service(
            app_state.slice(SecurityStateSlice).autonomy_change_strategy,
            "Autonomy Change Strategy",
        )
        strategy_granted = strategy.request_promotion(
            agent_key,
            requested_level,
        )

        # The approval queue stays the apply driver, but the strategy
        # verdict is now enforced, not audit-only: a granting strategy
        # produces an auto-decided (APPROVED) item and the registry
        # applies the level change. ``granted_by_strategy`` carries the
        # strategy's class name so the auto-decision is attributable;
        # ``None`` (HUMAN_ONLY) keeps the request pending for a human.
        result = await registry.update_autonomy(
            agent_key,
            AutonomyUpdate(
                requested_level=requested_level,
                reason=data.reason,
                # Guarded by require_ceo_or_manager: the human actor is
                # bound at the HTTP boundary, so attribute the request
                # to them for audit instead of dropping it as None.
                requested_by=NotBlankStr(resolve_decided_by()),
                granted_by_strategy=(
                    NotBlankStr(type(strategy).__name__) if strategy_granted else None
                ),
            ),
            approval_store=require_service(
                app_state.slice(ApprovalStateSlice).store, "Approval Store"
            ),
        )

        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_key,
            requested_level=requested_level.value,
            current_level=result.current_level.value,
            strategy_granted=strategy_granted,
            approval_id=result.approval_id,
        )

        return ApiResponse(
            data=AutonomyLevelResponse(
                agent_id=agent_id,
                level=result.current_level,
                promotion_pending=result.promotion_pending,
            ),
        )
