"""Analytics controller — derived read-only metrics."""

from collections import Counter

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import TaskStatus
from synthorg.observability import get_logger

logger = get_logger(__name__)


class OverviewMetrics(BaseModel):
    """High-level analytics overview.

    Attributes:
        total_tasks: Total number of tasks.
        tasks_by_status: Task counts grouped by status.
        total_agents: Number of configured agents.
        total_cost_usd: Total cost across all records.
    """

    model_config = ConfigDict(frozen=True)

    total_tasks: int = Field(ge=0, description="Total number of tasks")
    tasks_by_status: dict[str, int] = Field(
        description="Task counts by status (keys are TaskStatus values)",
    )
    total_agents: int = Field(ge=0, description="Number of configured agents")
    total_cost_usd: float = Field(ge=0.0, description="Total cost in USD")


class AnalyticsController(Controller):
    """Derived analytics and metrics."""

    path = "/analytics"
    tags = ("analytics",)

    @get("/overview", guards=[require_read_access])
    async def get_overview(
        self,
        state: State,
    ) -> ApiResponse[OverviewMetrics]:
        """Return high-level metrics overview.

        Args:
            state: Application state.

        Returns:
            Overview metrics envelope.
        """
        app_state: AppState = state.app_state

        all_tasks = await app_state.persistence.tasks.list_tasks()
        counts = Counter(t.status.value for t in all_tasks)
        by_status = {s.value: counts.get(s.value, 0) for s in TaskStatus}

        total_cost = await app_state.cost_tracker.get_total_cost()

        return ApiResponse(
            data=OverviewMetrics(
                total_tasks=len(all_tasks),
                tasks_by_status=by_status,
                total_agents=len(app_state.config.agents),
                total_cost_usd=total_cost,
            ),
        )
