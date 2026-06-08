"""Training plan and result repository protocols."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import (
    TrainingPlan,
    TrainingResult,
)
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)

__all__ = [
    "TrainingPlanFilterSpec",
    "TrainingPlanRepository",
    "TrainingResultRepository",
]


class TrainingPlanFilterSpec(BaseModel):
    """Filter spec for ``TrainingPlanRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by new agent ID",
    )
    status: str | None = Field(
        default=None,
        description="Filter by plan status (pending, executed, etc.)",
    )


@runtime_checkable
class TrainingPlanRepository(
    IdKeyedRepository["TrainingPlan", NotBlankStr],
    FilteredQueryRepository["TrainingPlan", TrainingPlanFilterSpec],
    Protocol,
):
    """CRUD + query interface for TrainingPlan persistence.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001).

    Bespoke D7 methods: :meth:`latest_pending`, :meth:`latest_by_agent`,
    :meth:`list_by_agent` are performance optimisations for agent-centric
    queries that are called frequently by the training service.
    """

    @override
    async def save(self, entity: TrainingPlan) -> None:
        """Persist a training plan (insert or update by id).

        Args:
            entity: The training plan to persist.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> TrainingPlan | None:
        """Retrieve a training plan by its ID.

        Args:
            entity_id: The plan identifier.

        Returns:
            The plan, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a training plan by ID.

        Args:
            entity_id: The plan identifier.

        Returns:
            ``True`` if the plan was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrainingPlan, ...]:
        """List training plans with pagination.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Plans ordered by id ascending.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: TrainingPlanFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrainingPlan, ...]:
        """List plans matching the filter spec.

        Args:
            filter_spec: Carries optional filters for agent_id, status.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching plans ordered by id ascending.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: TrainingPlanFilterSpec) -> int:
        """Count plans matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching plans.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def latest_pending(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created PENDING plan for an agent.

        Domain invariant: callers must use this to enforce the training
        workflow's decision to filter by pending status; a generic query
        cannot express this constraint.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The latest pending plan, or ``None`` if none exist.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def latest_by_agent(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created plan for an agent (any status).

        Domain invariant: the dashboard's training view depends on this
        method to rehydrate all plan history in a single call, regardless
        of status. A generic query would require the caller to manage
        status filtering.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The latest plan (by ``created_at`` DESC, then ``id`` DESC),
            or ``None`` if the agent has no plans yet.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[TrainingPlan, ...]:
        """Return plans for an agent ordered by created_at descending.

        Performance optimisation: indexed agent_id + created_at query
        pattern that is called frequently by the training service.

        Args:
            agent_id: Target agent identifier.
            limit: Maximum plans to return (must be >= 1).

        Returns:
            Tuple of plans ordered by ``created_at`` descending, capped
            at *limit* rows.

        Raises:
            QueryError: If the operation fails.
        """
        ...


@runtime_checkable
class TrainingResultRepository(
    IdKeyedRepository["TrainingResult", NotBlankStr],
    Protocol,
):
    """CRUD interface for TrainingResult persistence.

    Composes :class:`IdKeyedRepository` (ADR-0001).

    Bespoke D7 methods: :meth:`get_by_plan` and :meth:`get_latest`
    encode domain invariants that callers must not bypass. Results are
    keyed by their own ID but are always accessed through plan or agent
    lookups by the training service.
    """

    @override
    async def save(self, entity: TrainingResult) -> None:
        """Persist a training result (insert or update by id).

        Args:
            entity: The training result to persist.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> TrainingResult | None:
        """Retrieve a training result by its ID.

        Args:
            entity_id: The result identifier.

        Returns:
            The result, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a training result by ID.

        Args:
            entity_id: The result identifier.

        Returns:
            ``True`` if the result was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrainingResult, ...]:
        """List training results with pagination.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Results ordered by id ascending.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get_by_plan(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve the latest result by plan ID.

        Domain invariant: training workflow requires atomic lookup of
        the result corresponding to a plan. No generic query surface
        can express this key relationship without losing intent.

        Args:
            plan_id: Training plan identifier.

        Returns:
            The most recent result for the plan, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get_latest(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve the latest result for an agent.

        Domain invariant: callers must obtain the most recent training
        result for an agent by completed_at timestamp. A generic query
        would require the caller to manage sorting and filtering.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The most recent result (by completed_at), or ``None`` if none exist.

        Raises:
            QueryError: If the operation fails.
        """
        ...
