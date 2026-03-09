"""Budget controller — read-only access to cost data."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse, PaginatedResponse
from ai_company.api.pagination import PaginationLimit, PaginationOffset, paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.budget.config import BudgetConfig  # noqa: TC001
from ai_company.budget.cost_record import CostRecord  # noqa: TC001
from ai_company.observability import get_logger

logger = get_logger(__name__)


class BudgetController(Controller):
    """Read-only access to budget and cost data."""

    path = "/budget"
    tags = ("budget",)

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
    ) -> ApiResponse[dict[str, object]]:
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
            data={
                "agent_id": agent_id,
                "total_cost_usd": total,
            }
        )
