# module-kind: controller
"""Agent configuration listing and CRUD mutations at /agents."""

import json

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.api_core_state import org_mutation_service_of
from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.channels import CHANNEL_AGENTS, publish_ws_event
from synthorg.api.concurrency import compute_etag
from synthorg.api.controllers.agents._model_capabilities import (
    AgentConfigResponse,
    providers_for_capabilities,
    with_model_capabilities,
)
from synthorg.api.controllers.agents._shared import _DEFAULT_LIMIT, _config_agent_by_id
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
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    AGENT_DELETED_AUDIT,
    AGENT_DELETION_REQUESTED,
    AGENT_IDENTITY_MODIFIED,
)
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class AgentCrudController(Controller):
    """Agent configurations and CRUD mutations."""

    path = "/agents"
    tags = ("agents",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_agents(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[AgentConfigResponse]:
        """List all configured agents with their assigned model's capabilities.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated agent configurations.
        """
        app_state: AppState = state.app_state
        agents = await config_resolver_of(app_state).get_agents()
        # Paginate first, then resolve capabilities for the page only: the
        # provider index is built per request and a small-page client should
        # not pay for the whole roster. Pagination also rejects a tampered
        # cursor before the provider read, so a 400 costs nothing extra.
        page, meta = paginate_cursor(
            agents,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        providers = await providers_for_capabilities(app_state)
        return PaginatedResponse(
            data=with_model_capabilities(page, providers),
            pagination=meta,
        )

    @get("/{agent_id:str}")
    async def get_agent(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[AgentConfigResponse]:
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
        providers = await providers_for_capabilities(app_state)
        return ApiResponse(data=with_model_capabilities([found], providers)[0])

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
        request: Request[object, object, State],
        state: State,
        data: CreateAgentOrgRequest,
    ) -> ApiResponse[AgentConfigResponse]:
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
        # Build the response before announcing: publishing is fire-and-forget
        # and cannot be retracted, so projecting afterwards would let a
        # projection failure leave subscribers told of a create the requester
        # is shown as an error.
        providers = await providers_for_capabilities(app_state)
        created = with_model_capabilities([agent], providers)[0]
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
        return ApiResponse(data=created)

    @patch(
        "/{agent_id:str}",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("agents.update", key="user"),
        ],
    )
    async def update_agent(
        self,
        request: Request[object, object, State],
        state: State,
        agent_id: PathId,
        data: UpdateAgentOrgRequest,
    ) -> Response[ApiResponse[AgentConfigResponse]]:
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
        # Build the response before announcing the change: publishing is
        # fire-and-forget and cannot be retracted, so a failure after it would
        # leave subscribers told while the requester sees an error.
        providers = await providers_for_capabilities(app_state)
        projected = with_model_capabilities([updated], providers)[0]
        publish_ws_event(
            request,
            WsEventType.AGENT_UPDATED,
            CHANNEL_AGENTS,
            {"name": updated.name, "department": updated.department},
        )
        # The ETag is the concurrency token for the persisted config, so it
        # is computed from the config alone: model capabilities are derived
        # provider state and would otherwise invalidate a client's token
        # whenever an unrelated provider re-probe changed them.
        new_etag = compute_etag(
            json.dumps(
                updated.model_dump(mode="json"),
                sort_keys=True,
            ),
            "",
        )
        return Response(
            content=ApiResponse(data=projected),
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
        request: Request[object, object, State],
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
