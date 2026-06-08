"""Agent configuration, performance, activity, history, and CRUD mutations."""

import json
from typing import Any, Final, Self

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.api.api_core_state import org_mutation_service_of
from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.channels import CHANNEL_AGENTS, publish_ws_event
from synthorg.api.concurrency import compute_etag
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_org import (
    CreateAgentOrgRequest,
    UpdateAgentOrgRequest,
)
from synthorg.api.guards import (
    require_org_mutation,
    require_read_access,
)
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.config.schema import AgentConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.activity import (
    ActivityEvent,
    CareerEvent,
    filter_career_events,
    merge_activity_timeline,
)
from synthorg.hr.enums import AgentStatus, TrendDirection
from synthorg.hr.performance.summary import (
    AgentPerformanceSummary,
    extract_performance_summary,
)
from synthorg.hr.state import agent_registry_of, performance_tracker_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    AGENT_DELETED_AUDIT,
    AGENT_DELETION_REQUESTED,
    AGENT_IDENTITY_MODIFIED,
    API_AGENT_ACTIVITY_QUERIED,
    API_AGENT_HEALTH_QUERIED,
    API_AGENT_HEALTH_TREND_MISSING,
    API_AGENT_HISTORY_QUERIED,
    API_AGENT_PERFORMANCE_QUERIED,
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.persistence.state import persistence_of
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

# Safety cap for lifecycle event queries to prevent unbounded memory
# allocation.  The paginate() helper already caps the returned page
# to MAX_LIMIT, but the underlying fetch is uncapped without this.
_MAX_LIFECYCLE_EVENTS: Final[int] = 10_000


async def _require_registered_identity(
    app_state: AppState,
    agent_id: str,
) -> AgentIdentity:
    """Resolve a registered agent by its stable id.

    Args:
        app_state: Application state with agent registry.
        agent_id: Stable agent id from the URL path.

    Returns:
        The registered ``AgentIdentity``.

    Raises:
        NotFoundError: If no agent with *agent_id* is registered.
    """
    # str(agent.id) is canonical lowercase, so lowercase the path segment to
    # resolve case variants -- mirrors _config_agent_by_id so the registry-
    # backed routes don't 404 on an id the config route would resolve.
    canonical_agent_id = agent_id.lower()
    return require_resource_or_404(
        await agent_registry_of(app_state).get(canonical_agent_id),
        resource_type="agent",
        identifier=canonical_agent_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="read",
        extra_log_kwargs={"agent_id": canonical_agent_id},
    )


async def _config_agent_by_id(
    app_state: AppState,
    agent_id: str,
) -> AgentConfig:
    """Resolve a config-sourced agent by its stable id.

    Args:
        app_state: Application state with the config resolver.
        agent_id: Stable agent id from the URL path.

    Returns:
        The matching ``AgentConfig``.

    Raises:
        NotFoundError: If no configured agent has *agent_id*.
    """
    # str(agent.id) is canonical lowercase, so lowercase the path segment to
    # resolve case variants; a non-matching (or malformed) id falls through.
    target = agent_id.lower()
    agents = await config_resolver_of(app_state).get_agents()
    for agent in agents:
        if str(agent.id) == target:
            return agent
    msg = "Agent not found"
    logger.warning(API_RESOURCE_NOT_FOUND, resource="agent", agent_id=agent_id)
    raise NotFoundError(msg)


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


class AgentController(Controller):
    """Agent configurations, CRUD mutations, performance, and history."""

    path = "/agents"
    tags = ("agents",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_agents(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[AgentConfig]:
        """List all configured agents.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated agent configurations.
        """
        app_state: AppState = state.app_state
        agents = await config_resolver_of(app_state).get_agents()
        page, meta = paginate_cursor(
            agents,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{agent_id:str}")
    async def get_agent(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[AgentConfig]:
        """Get an agent by its stable id.

        Args:
            state: Application state.
            agent_id: Stable agent id to look up.

        Returns:
            Agent configuration envelope.

        Raises:
            NotFoundError: If the agent is not found.
        """
        app_state: AppState = state.app_state
        found = await _config_agent_by_id(app_state, agent_id)
        return ApiResponse(data=found)

    @post(
        "/",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("agents.create", key="user"),
        ],
        status_code=201,
    )
    async def create_agent(
        self,
        request: Request[Any, Any, Any],
        state: State,
        data: CreateAgentOrgRequest,
    ) -> ApiResponse[AgentConfig]:
        """Create a new agent in the org config.

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            data: Agent creation request.

        Returns:
            Created agent config envelope (HTTP 201).
        """
        app_state: AppState = state.app_state
        agent = await org_mutation_service_of(app_state).create_agent(data)
        publish_ws_event(
            request,
            WsEventType.AGENT_CREATED,
            CHANNEL_AGENTS,
            {
                "name": agent.name,
                "role": agent.role,
                "department": agent.department,
            },
        )
        return ApiResponse(data=agent)

    @patch(
        "/{agent_id:str}",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("agents.update", key="user"),
        ],
    )
    async def update_agent(
        self,
        request: Request[Any, Any, Any],
        state: State,
        agent_id: PathId,
        data: UpdateAgentOrgRequest,
    ) -> Response[ApiResponse[AgentConfig]]:
        """Update an existing agent.

        Supports optimistic concurrency via ``If-Match`` header.

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            agent_id: Stable agent id.
            data: Partial update request.

        Returns:
            Updated agent config envelope with ETag header.
        """
        app_state: AppState = state.app_state
        agent_name = (await _config_agent_by_id(app_state, agent_id)).name
        if_match = request.headers.get("if-match")
        updated = await org_mutation_service_of(app_state).update_agent(
            agent_name,
            data,
            if_match=if_match,
        )
        # Audit-chain entry: identity-bearing fields (name, role,
        # department, level, model, autonomy) just changed. The actor
        # is the request principal (stable user_id, matching the
        # workflows.py audit pattern); the field set is what the
        # request body declared via Pydantic ``model_fields_set``.
        # Log the persisted name (rename requests change it) and sort
        # the field set so the audit row is deterministic.
        logger.info(
            AGENT_IDENTITY_MODIFIED,
            agent_name=updated.name,
            previous_agent_name=agent_name,
            fields_changed=tuple(sorted(data.model_fields_set)),
            actor=get_authenticated_user_id(),
        )
        publish_ws_event(
            request,
            WsEventType.AGENT_UPDATED,
            CHANNEL_AGENTS,
            {"name": updated.name, "department": updated.department},
        )
        new_etag = compute_etag(
            json.dumps(
                updated.model_dump(mode="json"),
                sort_keys=True,
            ),
            "",
        )
        return Response(
            content=ApiResponse(data=updated),
            headers={"ETag": new_etag},
        )

    @delete(
        "/{agent_id:str}",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("agents.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_agent(
        self,
        request: Request[Any, Any, Any],
        state: State,
        agent_id: PathId,
    ) -> None:
        """Delete an agent from the org config.

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            agent_id: Stable agent id.
        """
        app_state: AppState = state.app_state
        agent_name = (await _config_agent_by_id(app_state, agent_id)).name
        actor = get_authenticated_user_id()
        # Pre-delete intent log -- fires BEFORE persistence so the
        # forensic audit chain captures the operator's request even if
        # the delete itself fails. ``AGENT_DELETED_AUDIT`` below confirms
        # actual successful deletion.
        logger.info(
            AGENT_DELETION_REQUESTED,
            agent_name=agent_name,
            actor=actor,
        )
        await org_mutation_service_of(app_state).delete_agent(agent_name)
        # Post-delete confirmation -- emitted only on persistence
        # success so the audit stream cannot record a "deleted" hop for
        # an agent that the database still holds.
        logger.info(
            AGENT_DELETED_AUDIT,
            agent_name=agent_name,
            actor=actor,
        )
        publish_ws_event(
            request,
            WsEventType.AGENT_DELETED,
            CHANNEL_AGENTS,
            {"name": agent_name},
        )

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
        except Exception as exc:
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
    snapshot: Any,
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
