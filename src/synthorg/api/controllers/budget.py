"""Budget controller -- read-only access to cost data."""

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Final, Self

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg._core.features import require_service
from synthorg.api.dto import (
    ApiResponse,
    PaginatedResponse,
)
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.call_analytics_models import (
    AnalyticsAggregation,
    PromptClassBreakdown,
)
from synthorg.budget.config import BudgetConfig
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, assert_currencies_match
from synthorg.budget.currency_resolver import resolve_currency
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_BUDGET_CALL_ANALYTICS_QUERIED,
    API_BUDGET_PROMPT_CLASS_BREAKDOWN_QUERIED,
    API_BUDGET_RECORDS_LISTED,
    API_VALIDATION_FAILED,
)
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

# Query-param aliases for the call-analytics filter surface. Module-level so
# the handler signatures stay short and the four orthogonal filters read once.
_AgentFilter = Annotated[
    str | None,
    QueryParameter(
        max_length=QUERY_MAX_LENGTH,
        description="Filter to calls emitted by this agent.",
    ),
]
_TaskFilter = Annotated[
    str | None,
    QueryParameter(
        max_length=QUERY_MAX_LENGTH,
        description="Filter to calls emitted under this task.",
    ),
]
_ProviderFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        max_length=QUERY_MAX_LENGTH,
        description="Filter to calls served by this provider.",
    ),
]
_PromptClassFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        max_length=QUERY_MAX_LENGTH,
        description="Filter to calls emitted by this prompt purpose.",
    ),
]
_StartFilter = Annotated[
    datetime | None,
    QueryParameter(description="Inclusive lower bound on record timestamp (ISO 8601)."),
]
_EndFilter = Annotated[
    datetime | None,
    QueryParameter(description="Exclusive upper bound on record timestamp (ISO 8601)."),
]


def _assume_utc(value: datetime | None) -> datetime | None:
    """Coerce an offset-less ISO query datetime to UTC.

    Stored record timestamps are UTC-aware; forwarding a naive value would
    raise on the aware/naive comparison in the breakdown scan. A naive input
    is assumed UTC (matching ``normalize_utc`` semantics) at this boundary.

    Returns:
        The value unchanged when aware or ``None``; otherwise the value with
        UTC attached.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    @computed_field(description="Average cost per record")
    @property
    def avg_cost(self) -> float:
        """Average cost per record (0.0 if no records)."""
        if self.record_count == 0:
            return 0.0
        return self.total_cost / self.record_count


class CostRecordListResponse(PaginatedResponse[CostRecord]):
    """Paginated cost records with summary aggregations.

    Extends the paginated envelope (``data`` is the page of cost records,
    plus the inherited ``error``/``error_detail``/``pagination``/``success``)
    with cost-specific aggregations computed over all matching records.

    Attributes:
        daily_summary: Per-day cost aggregations (all matching records).
        period_summary: Overall stats across all matching records.
        currency: ISO 4217 currency code.
    """

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
    def _currency_consistent(self) -> Self:
        """Reject a response whose summaries disagree on the currency.

        The top-level ``currency`` is authoritative; every per-day and the
        overall summary must report the same code, otherwise the dashboard
        would render mixed-currency totals as if they were one currency.

        Returns:
            The validated model.

        Raises:
            ValueError: If any summary currency diverges from ``currency``.
        """
        if self.period_summary.currency != self.currency:
            msg = (
                f"period_summary currency {self.period_summary.currency!r} "
                f"does not match response currency {self.currency!r}"
            )
            raise ValueError(msg)
        mismatched = [d.date for d in self.daily_summary if d.currency != self.currency]
        if mismatched:
            msg = f"daily_summary currency mismatch on dates: {mismatched}"
            raise ValueError(msg)
        return self


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
        budget = await config_resolver_of(app_state).get_budget_config()
        return ApiResponse(data=budget)

    @get("/records")
    async def list_cost_records(  # noqa: PLR0913 -- orthogonal filters + pagination
        self,
        state: State,
        agent_id: Annotated[
            str | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to cost records emitted by this agent.",
            ),
        ] = None,
        task_id: Annotated[
            str | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to cost records emitted under this task.",
            ),
        ] = None,
        prompt_class_id: _PromptClassFilter = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> CostRecordListResponse:
        """List cost records with optional filters and summaries.

        Summaries are computed from all matching records, not just
        the current page.

        Args:
            state: Application state.
            agent_id: Filter by agent.
            task_id: Filter by task.
            prompt_class_id: Filter by prompt purpose id.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated cost records with daily and period summaries.
        """
        app_state: AppState = state.app_state
        currency = await resolve_currency(config_resolver_of(app_state))
        records = await collect_all_records(
            require_service(
                app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
            ),
            agent_id=agent_id,
            task_id=task_id,
            prompt_class_id=prompt_class_id,
        )
        daily, period = _build_summaries(records, currency=currency)
        logger.info(
            API_BUDGET_RECORDS_LISTED,
            agent_id=agent_id,
            task_id=task_id,
            prompt_class_id=prompt_class_id,
            record_count=len(records),
        )
        page, meta = paginate_cursor(
            records,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return CostRecordListResponse(
            data=page,
            pagination=meta,
            daily_summary=daily,
            period_summary=period,
            currency=currency,
        )

    @get("/call-analytics")
    async def get_call_analytics(
        self,
        state: State,
        agent_id: _AgentFilter = None,
        task_id: _TaskFilter = None,
        provider: _ProviderFilter = None,
        prompt_class_id: _PromptClassFilter = None,
    ) -> ApiResponse[AnalyticsAggregation]:
        """Return aggregated per-call analytics over cost records.

        Args:
            state: Application state.
            agent_id: Filter by agent.
            task_id: Filter by task.
            provider: Filter by provider name.
            prompt_class_id: Filter by prompt purpose id.

        Returns:
            Aggregated per-call analytics envelope.
        """
        app_state: AppState = state.app_state
        aggregation = await require_service(
            app_state.slice(BudgetStateSlice).call_analytics_service,
            "Call Analytics Service",
        ).get_aggregation(
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            prompt_class_id=prompt_class_id,
        )
        logger.info(
            API_BUDGET_CALL_ANALYTICS_QUERIED,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            prompt_class_id=prompt_class_id,
            total_calls=aggregation.total_calls,
        )
        return ApiResponse(data=aggregation)

    @get(
        "/prompt-class-breakdown",
        guards=[
            per_op_rate_limit_from_policy("budget.prompt_class_breakdown", key="user")
        ],
    )
    async def get_prompt_class_breakdown(
        self,
        state: State,
        start: _StartFilter = None,
        end: _EndFilter = None,
    ) -> ApiResponse[PromptClassBreakdown]:
        """Return per-prompt-class cost + latency + quality breakdown.

        One row per prompt purpose with at least one matching cost record,
        so the operator can slice spend, latency, cache-hit, retry, and
        success by prompt purpose. ``start`` / ``end`` bound the scan so the
        full-ledger aggregation can be time-windowed.

        Args:
            state: Application state.
            start: Inclusive lower bound on record timestamp.
            end: Exclusive upper bound on record timestamp.

        Returns:
            Per-prompt-class breakdown envelope.
        """
        app_state: AppState = state.app_state
        breakdown = await require_service(
            app_state.slice(BudgetStateSlice).call_analytics_service,
            "Call Analytics Service",
        ).get_prompt_class_breakdown(start=_assume_utc(start), end=_assume_utc(end))
        logger.info(
            API_BUDGET_PROMPT_CLASS_BREAKDOWN_QUERIED,
            row_count=len(breakdown.rows),
        )
        return ApiResponse(data=breakdown)

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
        currency = await resolve_currency(config_resolver_of(app_state))
        total = await require_service(
            app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
        ).get_agent_cost(agent_id)
        return ApiResponse(
            data=AgentSpending(
                agent_id=agent_id,
                total_cost=total,
                currency=currency,
            ),
        )
