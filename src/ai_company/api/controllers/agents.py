"""Agent configuration controller."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.errors import NotFoundError
from ai_company.api.pagination import paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.config.schema import AgentConfig  # noqa: TC001


class AgentController(Controller):
    """Read-only access to agent configurations."""

    path = "/agents"
    tags = ("agents",)

    @get()
    async def list_agents(
        self,
        state: State,
        offset: int = 0,
        limit: int = 50,
    ) -> PaginatedResponse[AgentConfig]:
        """List all configured agents.

        Args:
            state: Application state.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Paginated agent configurations.
        """
        app_state: AppState = state.app_state
        page, meta = paginate(
            app_state.config.agents,
            offset=offset,
            limit=limit,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{agent_name:str}")
    async def get_agent(
        self,
        state: State,
        agent_name: str,
    ) -> ApiResponse[AgentConfig]:
        """Get an agent by name.

        Args:
            state: Application state.
            agent_name: Agent name to look up.

        Returns:
            Agent configuration envelope.

        Raises:
            NotFoundError: If the agent is not found.
        """
        app_state: AppState = state.app_state
        for agent in app_state.config.agents:
            if agent.name == agent_name:
                return ApiResponse(data=agent)
        msg = f"Agent {agent_name!r} not found"
        raise NotFoundError(msg)
