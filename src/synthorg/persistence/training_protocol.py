"""Training plan and result repository protocols.

Defines the persistence interface for training plans and results.
Concrete implementations (SQLite, Postgres) live in the backend-specific
sub-packages.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.training.models import (
    TrainingPlan,  # noqa: TC001
    TrainingResult,  # noqa: TC001
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class TrainingPlanRepository(Protocol):
    """CRUD and query interface for ``TrainingPlan`` persistence."""

    async def save(self, plan: TrainingPlan) -> None:
        """Persist a training plan (upsert by id).

        Args:
            plan: The training plan to save.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get(self, plan_id: NotBlankStr) -> TrainingPlan | None:
        """Retrieve a plan by ID.

        Args:
            plan_id: Training plan identifier.

        Returns:
            The plan, or ``None`` if not found.
        """
        ...

    async def latest_pending(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created PENDING plan for an agent.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The latest pending plan, or ``None`` if none exist.
        """
        ...

    async def latest_by_agent(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created plan for an agent.

        Unlike :meth:`latest_pending`, this does not filter on status;
        it returns the head of the plan history regardless of whether
        the plan is still pending, executed, or failed. Used to
        rehydrate the dashboard's training view after a reload.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The latest plan, or ``None`` if no plans exist.
        """
        ...

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[TrainingPlan, ...]:
        """Return plans for an agent ordered by created_at descending.

        Args:
            agent_id: Target agent identifier.
            limit: Maximum plans to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Tuple of plans (may be empty), capped at *limit* rows.
        """
        ...


@runtime_checkable
class TrainingResultRepository(Protocol):
    """CRUD and query interface for ``TrainingResult`` persistence."""

    async def save(self, result: TrainingResult) -> None:
        """Persist a training result (upsert by id).

        Args:
            result: The training result to save.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get_by_plan(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve a result by plan ID.

        Args:
            plan_id: Training plan identifier.

        Returns:
            The result, or ``None`` if not found.
        """
        ...

    async def get_latest(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve the latest result for an agent.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The most recent result (by completed_at), or ``None``.
        """
        ...
