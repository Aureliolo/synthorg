# module-kind: controller
"""Agent performance, activity, history, and health endpoints at /agents."""

from typing import Final, Self

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.api.controllers.agents._shared import (
    _DEFAULT_LIMIT,
    _require_registered_identity,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.state import AppState
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.activity import (
    ActivityEvent,
    CareerEvent,
    filter_career_events,
    merge_activity_timeline,
)
from synthorg.hr.enums import AgentStatus, TrendDirection
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.performance.summary import (
    AgentPerformanceSummary,
    extract_performance_summary,
)
from synthorg.hr.state import performance_tracker_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AGENT_ACTIVITY_QUERIED,
    API_AGENT_HEALTH_QUERIED,
    API_AGENT_HEALTH_TREND_MISSING,
    API_AGENT_HISTORY_QUERIED,
    API_AGENT_PERFORMANCE_QUERIED,
    API_REQUEST_ERROR,
)
from synthorg.persistence.state import persistence_of
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

# Safety cap for lifecycle event queries to prevent unbounded memory
# allocation.  The paginate() helper already caps the returned page
# to MAX_LIMIT, but the underlying fetch is uncapped without this.
_MAX_LIFECYCLE_EVENTS: Final[int] = 10_000


class TrustSummary(BaseModel):
    """Trust state summary for the health endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: ToolAccessLevel
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    last_evaluated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _score_requires_evaluation_time(self) -> Self:
        """Require ``last_evaluated_at`` whenever a ``score`` is set.

        Returns:
            The validated model instance.

        Raises:
            ValueError: If ``score`` is set but ``last_evaluated_at`` is None.
        """
        if self.score is not None and self.last_evaluated_at is None:
            msg = "score requires last_evaluated_at to be set"
            raise ValueError(msg)
        return self


class PerformanceSummary(BaseModel):
    """Performance snapshot summary for the health endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
    )
    collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
    )
    trend: TrendDirection | None = None

    @model_validator(mode="after")
    def _trend_requires_at_least_one_score(self) -> Self:
        """Require at least one component score whenever ``trend`` is set.

        Returns:
            The validated model instance.

        Raises:
            ValueError: If ``trend`` is set but both scores are None.
        """
        if (
            self.trend is not None
            and self.quality_score is None
            and self.collaboration_score is None
        ):
            msg = "trend requires at least one score to be set"
            raise ValueError(msg)
        return self


class AgentHealthResponse(BaseModel):
    """Composite health snapshot for a single agent."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    agent_name: NotBlankStr
    lifecycle_status: AgentStatus
    last_active_at: AwareDatetime | None = None
    trust: TrustSummary | None = None
    performance: PerformanceSummary | None = None


class AgentObservabilityController(Controller):
    """Agent performance, activity, history, and health reads."""

    path = "/agents"
    tags = ("agents",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/{agent_id:str}/performance")
    async def get_agent_performance(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[AgentPerformanceSummary]:
        """Get an agent's performance summary.

        Args:
            state: Application state.
            agent_id: Stable agent id to look up.

        Returns:
            Performance summary envelope.

        Raises:
            NotFoundError: If the agent is not found.
        """
        app_state: AppState = state.app_state
        identity = await _require_registered_identity(app_state, agent_id)
        # Drive downstream reads off the resolved canonical id so they key
        # the same record the registry matched, even for case variants.
        agent_id = str(identity.id)
        snapshot = await performance_tracker_of(app_state).get_snapshot(agent_id)
        summary = extract_performance_summary(snapshot, identity.name)
        logger.debug(
            API_AGENT_PERFORMANCE_QUERIED,
            agent_name=identity.name,
            tasks_total=summary.tasks_completed_total,
        )
        return ApiResponse(data=summary)

    @get("/{agent_id:str}/activity")
    async def get_agent_activity(
        self,
        state: State,
        agent_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ActivityEvent]:
        """Get an agent's activity timeline (paginated).

        Merges lifecycle events and task completion records into
        a single chronological timeline, most recent first.

        Args:
            state: Application state.
            agent_id: Stable agent id to look up.
            cursor: Opaque pagination cursor returned by the previous
                page; ``None`` starts at the beginning.
            limit: Page size.

        Returns:
            Paginated activity events.

        Raises:
            NotFoundError: If the agent is not found.
        """
        app_state: AppState = state.app_state
        identity = await _require_registered_identity(app_state, agent_id)
        # Drive downstream reads off the resolved canonical id so they key
        # the same record the registry matched, even for case variants.
        agent_id = str(identity.id)
        agent_name = identity.name
        lifecycle_events = await persistence_of(app_state).lifecycle_events.list_events(
            agent_id=agent_id,
            limit=_MAX_LIFECYCLE_EVENTS,
        )
        task_metrics = performance_tracker_of(app_state).get_task_metrics(
            agent_id=agent_id,
        )
        try:
            budget_cfg = await config_resolver_of(app_state).get_budget_config()
            currency = budget_cfg.currency
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="agents.activity",
                agent_name=agent_name,
                detail="budget config unavailable, using default currency",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            currency = DEFAULT_CURRENCY
        timeline = merge_activity_timeline(
            lifecycle_events=lifecycle_events,
            task_metrics=task_metrics,
            currency=currency,
        )
        page, meta = paginate_cursor(
            timeline,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        logger.debug(
            API_AGENT_ACTIVITY_QUERIED,
            agent_name=agent_name,
            returned_events=len(page),
            has_more=meta.has_more,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{agent_id:str}/history")
    async def get_agent_history(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[tuple[CareerEvent, ...]]:
        """Get an agent's career history.

        Returns career-relevant lifecycle events (hired, fired,
        promoted, demoted, onboarded) in chronological order.

        Args:
            state: Application state.
            agent_id: Stable agent id to look up.

        Returns:
            Career events envelope.

        Raises:
            NotFoundError: If the agent is not found.
        """
        app_state: AppState = state.app_state
        identity = await _require_registered_identity(app_state, agent_id)
        # Drive downstream reads off the resolved canonical id so they key
        # the same record the registry matched, even for case variants.
        agent_id = str(identity.id)
        # No limit here: career events are few per agent and the filter
        # below keeps only ~5 event types; capping would risk dropping
        # older milestones (e.g. the original HIRED event).
        events = await persistence_of(app_state).lifecycle_events.list_events(
            agent_id=agent_id,
        )
        career = filter_career_events(events)
        logger.debug(
            API_AGENT_HISTORY_QUERIED,
            agent_name=identity.name,
            career_events=len(career),
        )
        return ApiResponse(data=career)

    @get("/{agent_id:str}/health")
    async def get_agent_health(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[AgentHealthResponse]:
        """Get composite health for an agent.

        Combines performance snapshot, trust state, and lifecycle
        status into a single response.

        Args:
            state: Application state.
            agent_id: Stable agent id to look up.

        Returns:
            Agent health envelope.

        Raises:
            NotFoundError: If the agent is not found.
        """
        app_state: AppState = state.app_state
        identity = await _require_registered_identity(app_state, agent_id)
        # Drive downstream reads off the resolved canonical id so they key
        # the same record the registry matched, even for case variants.
        agent_id = str(identity.id)

        snapshot = await performance_tracker_of(app_state).get_snapshot(agent_id)
        trend = _extract_quality_trend(snapshot)
        perf = PerformanceSummary(
            quality_score=snapshot.overall_quality_score,
            collaboration_score=snapshot.overall_collaboration_score,
            trend=trend,
        )

        trust: TrustSummary | None = None
        trust_service = app_state.slice(SecurityStateSlice).trust_service
        if trust_service is not None:
            trust_state = trust_service.get_trust_state(agent_id)
            if trust_state is not None:
                trust = TrustSummary(
                    level=trust_state.global_level,
                    score=trust_state.trust_score,
                    last_evaluated_at=trust_state.last_evaluated_at,
                )

        # Derive last_active_at from most recent lifecycle event.
        last_active_at: AwareDatetime | None = None
        events = await persistence_of(app_state).lifecycle_events.list_events(
            agent_id=agent_id,
            limit=1,
        )
        if events:
            last_active_at = events[0].timestamp

        health = AgentHealthResponse(
            agent_id=agent_id,
            agent_name=str(identity.name),
            lifecycle_status=identity.status,
            last_active_at=last_active_at,
            trust=trust,
            performance=perf,
        )
        logger.info(
            API_AGENT_HEALTH_QUERIED,
            agent_name=identity.name,
        )
        return ApiResponse(data=health)


def _extract_quality_trend(
    snapshot: AgentPerformanceSnapshot,
) -> TrendDirection | None:
    """Extract the quality trend direction from a performance snapshot.

    Args:
        snapshot: Performance snapshot with a ``trends`` collection
            (typically from ``PerformanceTracker.get_snapshot``).

    Returns:
        The ``TrendDirection`` for the "quality" metric, or ``None``
        if no quality trend is recorded in the snapshot.
    """
    for t in snapshot.trends:
        if t.metric_name == "quality":
            direction: TrendDirection = t.direction
            return direction
    logger.debug(
        API_AGENT_HEALTH_TREND_MISSING,
        trend_count=len(snapshot.trends),
    )
    return None
