"""Budget controller — read-only access to cost data."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import PaginationLimit, PaginationOffset, paginate
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.config import BudgetConfig  # noqa: TC001
from synthorg.budget.cost_record import CostRecord  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger

logger = get_logger(__name__)


class AgentSpending(BaseModel):
    """Total spending for a single agent.

    Attributes:
        agent_id: Agent identifier.
        total_cost_usd: Cumulative cost in USD.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    total_cost_usd: float = Field(ge=0.0, description="Total cost in USD")


class BudgetController(Controller):
    """Read-only access to budget and cost data."""

    path = "/budget"
    tags = ("budget",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/config")
    async def get_budget_config(
        self,
        state: State,
    ) -> ApiResponse[BudgetConfig]:
        """Return the budget configuration.

        Args:
            state: Application state.

        Returns:
            Budget config envelope.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=app_state.config.budget)

    @get("/records")
    async def list_cost_records(
        self,
        state: State,
        agent_id: str | None = None,
        task_id: str | None = None,
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
    ) -> PaginatedResponse[CostRecord]:
        """List cost records with optional filters.

        Args:
            state: Application state.
            agent_id: Filter by agent.
            task_id: Filter by task.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Paginated cost record list.
        """
        app_state: AppState = state.app_state
        records = await app_state.cost_tracker.get_records(
            agent_id=agent_id,
            task_id=task_id,
        )
        page, meta = paginate(records, offset=offset, limit=limit)
        return PaginatedResponse(data=page, pagination=meta)

    @get("/agents/{agent_id:str}")
    async def get_agent_spending(
        self,
        state: State,
        agent_id: str,
    ) -> ApiResponse[AgentSpending]:
        """Get total spending for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Agent spending envelope.
        """
        app_state: AppState = state.app_state
        total = await app_state.cost_tracker.get_agent_cost(agent_id)
        return ApiResponse(
            data=AgentSpending(
                agent_id=agent_id,
                total_cost_usd=total,
            ),
        )
