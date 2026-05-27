"""Scaling controller -- REST endpoints for dynamic company scaling.

Exposes scaling strategies, decisions, signals, and manual
evaluation triggers.
"""

from typing import Annotated

from litestar import Controller, get, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.params import PathParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.dto import (
    DEFAULT_LIMIT,
    ApiResponse,
    PaginatedResponse,
    PaginationMeta,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingStrategyName
from synthorg.hr.scaling.models import (  # noqa: TC001
    ScalingDecision,
    ScalingSignal,
)
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_SCALING_CONTROLLER_INVALID_REQUEST,
    HR_SCALING_CONTROLLER_SERVICE_MISSING,
    HR_SCALING_MANUAL_TRIGGER_REQUESTED,
    HR_SCALING_PRIORITY_ORDER_UPDATED,
    HR_SCALING_STRATEGY_TOGGLED,
)
from synthorg.settings.definitions.api import SCALING_STRATEGY_PRIORITY_FALLBACK

logger = get_logger(__name__)

# Once-per-process flag: the scaling subsystem has no construction site
# in ``create_app`` today, so every request to a scaling endpoint sees a
# ``None`` service. Without this gate the controller fired one WARNING
# per request, drowning real signal in noise. The first request that
# notices the missing wire logs a single INFO so operators still see
# "scaling is dormant"; subsequent requests stay silent.
_SCALING_MISSING_LOGGED = False


def _note_scaling_missing() -> None:
    """Log a single INFO when scaling service is observed as unwired."""
    global _SCALING_MISSING_LOGGED  # noqa: PLW0603
    if _SCALING_MISSING_LOGGED:
        return
    _SCALING_MISSING_LOGGED = True
    logger.info(
        HR_SCALING_CONTROLLER_SERVICE_MISSING,
        note=(
            "Scaling service is not configured; endpoints return empty "
            "data until the subsystem is wired in create_app."
        ),
    )


# -- Response DTOs -----------------------------------------------------------


class ScalingStrategyResponse(BaseModel):
    """Strategy summary for API responses."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Strategy identifier")
    enabled: bool = Field(description="Whether this strategy is active")
    priority: int = Field(ge=0, description="Priority rank")


class ScalingSignalResponse(BaseModel):
    """Signal value for API responses."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Signal name")
    value: float = Field(description="Current value")
    source: NotBlankStr = Field(description="Signal source")
    threshold: float | None = Field(
        default=None,
        description="Configured threshold for this signal",
    )
    timestamp: NotBlankStr = Field(description="ISO timestamp when collected")


class ScalingDecisionResponse(BaseModel):
    """Decision summary for API responses."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Decision identifier")
    action_type: NotBlankStr = Field(description="Action type")
    source_strategy: NotBlankStr = Field(description="Strategy that proposed this")
    target_agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent targeted for pruning",
    )
    target_role: NotBlankStr | None = Field(
        default=None,
        description="Role to hire for",
    )
    target_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skills required for the hire target",
    )
    target_department: NotBlankStr | None = Field(
        default=None,
        description="Department for the hire target",
    )
    rationale: str = Field(description="Decision rationale")
    confidence: float = Field(description="Strategy confidence")
    signals: tuple[ScalingSignalResponse, ...] = Field(
        default=(),
        description="Signals that informed the decision",
    )
    created_at: NotBlankStr = Field(description="ISO timestamp")


class StrategyUpdateRequest(BaseModel):
    """Request body for enabling/disabling a strategy."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(description="Whether the strategy should be active")


class PriorityUpdateRequest(BaseModel):
    """Request body for updating priority order."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    order: tuple[NotBlankStr, ...] = Field(
        description="Strategy names in priority order (first = highest)",
    )


def _signal_to_response(s: ScalingSignal) -> ScalingSignalResponse:
    """Convert a domain signal to a response DTO.

    Returns:
        ``ScalingSignalResponse`` instance.
    """
    return ScalingSignalResponse(
        name=str(s.name),
        value=s.value,
        source=str(s.source),
        threshold=s.threshold,
        timestamp=s.timestamp.isoformat(),
    )


def _decision_to_response(d: ScalingDecision) -> ScalingDecisionResponse:
    """Convert a domain decision to a response DTO.

    Returns:
        ``ScalingDecisionResponse`` instance.
    """
    return ScalingDecisionResponse(
        id=str(d.id),
        action_type=d.action_type.value,
        source_strategy=d.source_strategy.value,
        target_agent_id=str(d.target_agent_id) if d.target_agent_id else None,
        target_role=str(d.target_role) if d.target_role else None,
        target_skills=tuple(str(s) for s in d.target_skills),
        target_department=(str(d.target_department) if d.target_department else None),
        rationale=str(d.rationale),
        confidence=d.confidence,
        signals=tuple(_signal_to_response(s) for s in d.signals),
        created_at=d.created_at.isoformat(),
    )


# -- Controller --------------------------------------------------------------


class ScalingController(Controller):
    """Dynamic company scaling endpoints."""

    path = "/scaling"
    tags = ("scaling",)

    @get("/strategies", guards=[require_read_access])
    async def list_strategies(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ScalingStrategyResponse]:
        """List all scaling strategies with their current status, paginated by name.

        Args:
            state: Application state.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated strategy list with enabled/priority info.
        """
        app_state: AppState = state.app_state
        scaling = app_state.slice(HrStateSlice).scaling_service
        if scaling is None:
            _note_scaling_missing()
            return PaginatedResponse[ScalingStrategyResponse](
                data=(),
                pagination=PaginationMeta(
                    limit=limit,
                    next_cursor=None,
                    has_more=False,
                ),
            )

        config = scaling.config
        configured_order = {
            name.value: idx for idx, name in enumerate(config.priority_order)
        }

        ordered = tuple(
            ScalingStrategyResponse(
                name=str(s.name),
                enabled=scaling.is_strategy_enabled(str(s.name)),
                priority=configured_order.get(
                    str(s.name),
                    SCALING_STRATEGY_PRIORITY_FALLBACK,
                ),
            )
            for s in sorted(scaling.strategies, key=lambda s: str(s.name))
        )
        page, meta = paginate_cursor(
            ordered,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[ScalingStrategyResponse](data=page, pagination=meta)

    @get("/decisions", guards=[require_read_access])
    async def list_decisions(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ScalingDecisionResponse]:
        """List recent scaling decisions.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated list of recent decisions.
        """
        app_state: AppState = state.app_state
        scaling = app_state.slice(HrStateSlice).scaling_service
        if scaling is None:
            _note_scaling_missing()
            return PaginatedResponse[ScalingDecisionResponse](
                data=(),
                pagination=PaginationMeta(
                    limit=limit,
                    next_cursor=None,
                    has_more=False,
                ),
            )

        decisions = sorted(
            scaling.get_recent_decisions(),
            key=lambda d: d.created_at,
            reverse=True,
        )
        responses = tuple(_decision_to_response(d) for d in decisions)
        page, meta = paginate_cursor(
            responses,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[ScalingDecisionResponse](data=page, pagination=meta)

    @get("/signals", guards=[require_read_access])
    async def list_signals(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ScalingSignalResponse]:
        """Get current signal values for dashboard display, paginated by name.

        Args:
            state: Application state.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated signal values from all sources.
        """
        app_state: AppState = state.app_state
        scaling = app_state.slice(HrStateSlice).scaling_service
        if scaling is None:
            _note_scaling_missing()
            return PaginatedResponse[ScalingSignalResponse](
                data=(),
                pagination=PaginationMeta(
                    limit=limit,
                    next_cursor=None,
                    has_more=False,
                ),
            )

        # Read live signals from the most recently built context. We
        # fall back to decision history when no context has been built
        # yet so the dashboard still shows something on cold start.
        signals: list[ScalingSignalResponse] = []
        seen: set[str] = set()

        live_context = scaling.get_last_context()
        if live_context is not None:
            for group in (
                live_context.workload_signals,
                live_context.budget_signals,
                live_context.performance_signals,
                live_context.skill_signals,
            ):
                for signal in group:
                    name_str = str(signal.name)
                    if name_str not in seen:
                        seen.add(name_str)
                        signals.append(_signal_to_response(signal))
        else:
            for decision in reversed(scaling.get_recent_decisions()):
                for signal in decision.signals:
                    name_str = str(signal.name)
                    if name_str not in seen:
                        seen.add(name_str)
                        signals.append(_signal_to_response(signal))
        ordered = tuple(sorted(signals, key=lambda s: str(s.name)))
        page, meta = paginate_cursor(
            ordered,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[ScalingSignalResponse](data=page, pagination=meta)

    @post(
        "/evaluate",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy(
                "scaling.trigger_evaluation",
                key="user",
            ),
        ],
    )
    async def trigger_evaluation(
        self,
        state: State,
    ) -> ApiResponse[tuple[ScalingDecisionResponse, ...]]:
        """Manually trigger a scaling evaluation cycle.

        Args:
            state: Application state.

        Returns:
            Decisions produced by the evaluation.
        """
        app_state: AppState = state.app_state
        scaling = app_state.slice(HrStateSlice).scaling_service
        if scaling is None:
            _note_scaling_missing()
            return ApiResponse(
                data=(),
                error="Scaling service not configured",
            )

        logger.info(HR_SCALING_MANUAL_TRIGGER_REQUESTED)

        # Get active agents from registry.
        registry = require_service(
            app_state.slice(HrStateSlice).agent_registry, "Agent Registry"
        )
        agents = await registry.list_active()
        agent_ids = tuple(NotBlankStr(str(a.id)) for a in agents)

        decisions = await scaling.evaluate(agent_ids=agent_ids)
        responses = tuple(_decision_to_response(d) for d in decisions)
        return ApiResponse(data=responses)

    @put(
        "/strategies/{strategy_name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("scaling.update_strategy", key="user"),
        ],
    )
    async def update_strategy(
        self,
        state: State,
        strategy_name: Annotated[str, PathParameter()],
        data: StrategyUpdateRequest,
    ) -> ApiResponse[ScalingStrategyResponse]:
        """Enable or disable a scaling strategy.

        Args:
            state: Application state.
            strategy_name: Strategy name to update.
            data: Update payload with enabled flag.

        Returns:
            Updated strategy status.
        """
        app_state: AppState = state.app_state
        scaling = app_state.slice(HrStateSlice).scaling_service
        if scaling is None:
            _note_scaling_missing()
            return ApiResponse(
                data=None,
                error="Scaling service not configured",
            )

        known = {str(s.name) for s in scaling.strategies}
        if strategy_name not in known:
            logger.warning(
                HR_SCALING_CONTROLLER_INVALID_REQUEST,
                endpoint="update_strategy",
                reason="unknown_strategy",
                strategy=strategy_name,
                known=sorted(known),
            )
            return ApiResponse(
                data=None,
                error=f"Unknown strategy: {strategy_name}",
            )

        scaling.set_strategy_enabled(strategy_name, enabled=data.enabled)
        logger.info(
            HR_SCALING_STRATEGY_TOGGLED,
            strategy=strategy_name,
            enabled=data.enabled,
        )

        config = scaling.config
        configured_order = {
            name.value: idx for idx, name in enumerate(config.priority_order)
        }
        return ApiResponse(
            data=ScalingStrategyResponse(
                name=strategy_name,
                enabled=scaling.is_strategy_enabled(strategy_name),
                priority=configured_order.get(
                    strategy_name,
                    SCALING_STRATEGY_PRIORITY_FALLBACK,
                ),
            ),
        )

    @put(
        "/priority",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("scaling.update_priority", key="user"),
        ],
    )
    async def update_priority(
        self,
        state: State,
        data: PriorityUpdateRequest,
    ) -> ApiResponse[tuple[str, ...]]:
        """Update the conflict resolution priority order.

        Args:
            state: Application state.
            data: New priority order (strategy names, first = highest).

        Returns:
            Updated priority order.
        """
        app_state: AppState = state.app_state
        scaling = app_state.slice(HrStateSlice).scaling_service
        if scaling is None:
            _note_scaling_missing()
            return ApiResponse(
                data=(),
                error="Scaling service not configured",
            )

        try:
            order = tuple(ScalingStrategyName(n) for n in data.order)
        except ValueError as exc:
            logger.warning(
                HR_SCALING_CONTROLLER_INVALID_REQUEST,
                endpoint="update_priority",
                reason="invalid_priority_order",
                order=list(data.order),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # HTTP body carries a generic message: the structured detail
            # is on the WARNING log above. Returning the raw exception
            # type prefix (`safe_error_description` includes
            # `{ExceptionType}: ...`) over the wire would leak internal
            # class names without operator benefit (CWE-200); operators
            # debug via the log, not the response body.
            return ApiResponse(data=(), error="Invalid priority order")

        try:
            scaling.update_priority_order(order)
        except ValueError as exc:
            logger.warning(
                HR_SCALING_CONTROLLER_INVALID_REQUEST,
                endpoint="update_priority",
                reason="invalid_priority_order",
                order=list(data.order),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ApiResponse(data=(), error="Invalid priority order")
        logger.info(
            HR_SCALING_PRIORITY_ORDER_UPDATED,
            order=[n.value for n in order],
        )
        return ApiResponse(
            data=tuple(n.value for n in order),
        )
