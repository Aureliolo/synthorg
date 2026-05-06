"""Budget controller -- read-only access to cost data."""

import math
from collections import defaultdict
from typing import Annotated, Self

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.api.dto import (
    ApiResponse,
    ErrorDetail,
    PaginationMeta,
)
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.config import BudgetConfig  # noqa: TC001
from synthorg.budget.cost_record import CostRecord  # noqa: TC001
from synthorg.budget.currency import DEFAULT_CURRENCY, assert_currencies_match
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_BUDGET_RECORDS_LISTED,
    API_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class AgentSpending(BaseModel):
    """Total spending for a single agent.

    Attributes:
        agent_id: Agent identifier.
        total_cost: Cumulative cost in the configured currency.
        currency: ISO 4217 currency code.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier")
    total_cost: float = Field(
        ge=0.0, description="Total cost in the configured currency"
    )
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )


class DailySummary(BaseModel):
    """Per-day cost aggregation.

    Attributes:
        date: ISO date string (YYYY-MM-DD).
        total_cost: Sum of cost for the day.
        total_input_tokens: Sum of input tokens for the day.
        total_output_tokens: Sum of output tokens for the day.
        record_count: Number of cost records on this day.
        currency: ISO 4217 currency code.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    date: NotBlankStr = Field(description="ISO date (YYYY-MM-DD)")
    total_cost: float = Field(
        ge=0.0, description="Total cost in the configured currency"
    )
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )
    total_input_tokens: int = Field(
        ge=0,
        description="Total input tokens",
    )
    total_output_tokens: int = Field(
        ge=0,
        description="Total output tokens",
    )
    record_count: int = Field(ge=0, description="Number of records")


class PeriodSummary(BaseModel):
    """Overall stats across all matching cost records.

    Attributes:
        total_cost: Sum of cost across all records.
        total_input_tokens: Sum of input tokens.
        total_output_tokens: Sum of output tokens.
        record_count: Total number of records.
        avg_cost: Average cost per record (computed, 0.0 if none).
        currency: ISO 4217 currency code.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    total_cost: float = Field(
        ge=0.0, description="Total cost in the configured currency"
    )
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )
    total_input_tokens: int = Field(
        ge=0,
        description="Total input tokens",
    )
    total_output_tokens: int = Field(
        ge=0,
        description="Total output tokens",
    )
    record_count: int = Field(ge=0, description="Number of records")

    @computed_field(description="Average cost per record")  # type: ignore[prop-decorator]
    @property
    def avg_cost(self) -> float:
        """Average cost per record (0.0 if no records)."""
        if self.record_count == 0:
            return 0.0
        return self.total_cost / self.record_count


class CostRecordListResponse(BaseModel):
    """Paginated cost records with summary aggregations.

    ``error`` and ``error_detail`` must both be set or both be ``None``.

    Attributes:
        data: Page of cost records.
        error: Error message (``None`` on success).
        error_detail: Structured error metadata (``None`` on success).
        pagination: Pagination metadata.
        daily_summary: Per-day cost aggregations (all matching records).
        period_summary: Overall stats across all matching records.
        success: Whether the request succeeded (computed from ``error``).
        currency: ISO 4217 currency code.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    data: tuple[CostRecord, ...] = ()
    error: str | None = None
    error_detail: ErrorDetail | None = None
    pagination: PaginationMeta
    daily_summary: tuple[DailySummary, ...] = ()
    period_summary: PeriodSummary
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code",
    )

    @model_validator(mode="after")
    def _validate_error_detail_consistency(self) -> Self:
        """Ensure ``error`` and ``error_detail`` are set together."""
        if self.error_detail is not None and self.error is None:
            msg = "error_detail requires error to be set"
            raise ValueError(msg)
        if self.error is not None and self.error_detail is None:
            msg = "error must be accompanied by error_detail"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the request succeeded (derived from ``error``)."""
        return self.error is None


def _build_summaries(
    records: tuple[CostRecord, ...],
    *,
    currency: str = DEFAULT_CURRENCY,
) -> tuple[tuple[DailySummary, ...], PeriodSummary]:
    """Compute daily and period summaries from cost records.

    Args:
        records: All filtered cost records (not just the current page).
        currency: ISO 4217 currency code for response models.

    Returns:
        Tuple of (daily summaries sorted chronologically, period summary).

    Raises:
        MixedCurrencyAggregationError: If the records span more than
            one currency, or if any record's currency does not match
            the requested ``currency``.  Cost summation across
            currencies is meaningless without an FX policy and is
            rejected at the aggregator boundary -- the caller must
            scope the query to a single-currency window.
    """
    if not records:
        return (), PeriodSummary(
            total_cost=0.0,
            total_input_tokens=0,
            total_output_tokens=0,
            record_count=0,
            currency=currency,
        )

    record_currency = assert_currencies_match(r.currency for r in records)
    if record_currency is not None and record_currency != currency:
        logger.warning(
            API_VALIDATION_FAILED,
            reason="mixed_currency_aggregation",
            scope="budget_summary",
            requested_currency=currency,
            record_currencies=[record_currency],
            record_count=len(records),
        )
        msg = (
            f"Cost records denominated in {record_currency!r}; "
            f"summary requested in {currency!r}"
        )
        raise MixedCurrencyAggregationError(
            msg,
            currencies=frozenset({record_currency, currency}),
        )

    by_day: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_day[r.timestamp.date().isoformat()].append(r)

    # day_records is a by-day partition of records; the upstream guard
    # above already verified all records share one currency, so each
    # partition trivially does too -- the per-day fsum below cannot
    # observe a mixed input.
    daily = tuple(
        DailySummary(
            date=date,
            # lint-allow: currency-aggregation -- partitioned upstream
            total_cost=math.fsum(r.cost for r in day_records),
            total_input_tokens=sum(r.input_tokens for r in day_records),
            total_output_tokens=sum(r.output_tokens for r in day_records),
            record_count=len(day_records),
            currency=currency,
        )
        for date, day_records in sorted(by_day.items())
    )

    period = PeriodSummary(
        total_cost=math.fsum(r.cost for r in records),
        total_input_tokens=sum(r.input_tokens for r in records),
        total_output_tokens=sum(r.output_tokens for r in records),
        record_count=len(records),
        currency=currency,
    )

    return daily, period


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
        budget = await app_state.config_resolver.get_budget_config()
        return ApiResponse(data=budget)

    @get("/records")
    async def list_cost_records(
        self,
        state: State,
        agent_id: Annotated[str, Parameter(max_length=QUERY_MAX_LENGTH)] | None = None,
        task_id: Annotated[str, Parameter(max_length=QUERY_MAX_LENGTH)] | None = None,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> CostRecordListResponse:
        """List cost records with optional filters and summaries.

        Summaries are computed from all matching records, not just
        the current page.

        Args:
            state: Application state.
            agent_id: Filter by agent.
            task_id: Filter by task.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated cost records with daily and period summaries.
        """
        # Manual check retained: Litestar Parameter(max_length=...) on
        # query params crashes the worker instead of returning a proper
        # RFC 9457 error response.
        for field_name, value in (("agent_id", agent_id), ("task_id", task_id)):
            if value is not None and len(value) > QUERY_MAX_LENGTH:
                msg = f"{field_name} exceeds maximum length of {QUERY_MAX_LENGTH}"
                logger.warning(
                    API_VALIDATION_FAILED,
                    field=field_name,
                    actual_length=len(value),
                    max_length=QUERY_MAX_LENGTH,
                )
                raise ValidationError(msg)

        app_state: AppState = state.app_state
        budget_cfg = await app_state.config_resolver.get_budget_config()
        currency = budget_cfg.currency
        records = await app_state.cost_tracker.get_records(
            agent_id=agent_id,
            task_id=task_id,
        )
        daily, period = _build_summaries(records, currency=currency)
        logger.info(
            API_BUDGET_RECORDS_LISTED,
            agent_id=agent_id,
            task_id=task_id,
            record_count=len(records),
        )
        page, meta = paginate_cursor(
            records,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return CostRecordListResponse(
            data=page,
            pagination=meta,
            daily_summary=daily,
            period_summary=period,
            currency=currency,
        )

    @get("/agents/{agent_id:str}")
    async def get_agent_spending(
        self,
        state: State,
        agent_id: PathId,
    ) -> ApiResponse[AgentSpending]:
        """Get total spending for an agent.

        Args:
            state: Application state.
            agent_id: Agent identifier.

        Returns:
            Agent spending envelope.
        """
        app_state: AppState = state.app_state
        budget_cfg = await app_state.config_resolver.get_budget_config()
        total = await app_state.cost_tracker.get_agent_cost(agent_id)
        return ApiResponse(
            data=AgentSpending(
                agent_id=agent_id,
                total_cost=total,
                currency=budget_cfg.currency,
            ),
        )
