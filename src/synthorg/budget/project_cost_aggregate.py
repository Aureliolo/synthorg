"""Durable per-project cost aggregate value object.

Stores lifetime cost totals per project, surviving the in-memory
CostTracker's 168-hour retention window.  Updated atomically on
each cost recording; queried by BudgetEnforcer for project-level
budget enforcement.

The repository Protocol for this value object lives in
``synthorg.persistence.project_cost_aggregate_protocol`` per the
persistence-boundary convention -- concrete backends implement that
Protocol and expose it via ``PersistenceBackend.project_cost_aggregates``.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.budget.currency import CurrencyCode  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class ProjectCostAggregate(BaseModel):
    """Immutable snapshot of a project's lifetime cost totals.

    One row per project in the ``project_cost_aggregates`` table.
    Totals are monotonically increasing (never pruned).  Every
    aggregate carries a single currency code; mixing currencies is
    rejected at the repository boundary via
    :class:`MixedCurrencyAggregationError`.

    Attributes:
        project_id: Unique project identifier (primary key).
        total_cost: Accumulated cost denominated in ``currency``.
        currency: ISO 4217 currency code for ``total_cost``.
        total_input_tokens: Accumulated input token count.
        total_output_tokens: Accumulated output token count.
        record_count: Number of cost records aggregated.
        last_updated: Timestamp of the most recent increment.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    project_id: NotBlankStr = Field(description="Project identifier")
    total_cost: float = Field(
        ge=0.0,
        description="Accumulated cost denominated in ``currency``",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for ``total_cost``",
    )
    total_input_tokens: int = Field(
        ge=0,
        description="Accumulated input tokens",
    )
    total_output_tokens: int = Field(
        ge=0,
        description="Accumulated output tokens",
    )
    record_count: int = Field(
        ge=0,
        description="Number of cost records aggregated",
    )
    last_updated: AwareDatetime = Field(
        description="Timestamp of last increment",
    )


# Repository Protocol moved to
# ``synthorg.persistence.project_cost_aggregate_protocol`` per the
# persistence-boundary convention; importers should reach for it there.
