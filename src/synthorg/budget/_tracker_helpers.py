"""Pure helper functions for the cost tracker.

Extracted from :mod:`synthorg.budget.tracker` to keep that module under
the 800-line limit after the same-currency invariant pushed it over.
These helpers are all pure and framework-agnostic (filtering,
aggregation, and the same-currency guard) so they belong in a leaf
module that the tracker composes.

The tracker owns all state; this module is intentionally stateless.
"""

from collections.abc import Sequence  # noqa: TC003 -- runtime type
from datetime import datetime  # noqa: TC003 -- runtime type
from typing import NamedTuple

from synthorg.budget._aggregation import group_by_agent, sum_cost
from synthorg.budget.cost_record import CostRecord  # noqa: TC001 -- runtime use
from synthorg.budget.currency import assert_currencies_match
from synthorg.budget.spending_summary import AgentSpending
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime use in filter
from synthorg.observability import get_logger
from synthorg.observability.events.budget import BUDGET_TIME_RANGE_INVALID

logger = get_logger(__name__)


class _AggregateResult(NamedTuple):
    """Aggregated cost and token totals.

    ``currency`` is ``None`` only when ``record_count == 0``.  Any
    non-empty aggregation carries the single currency that every input
    record shared; mixed-currency input raises
    :class:`~synthorg.budget.errors.MixedCurrencyAggregationError` at
    the aggregator before this tuple is constructed.
    """

    cost: float
    currency: str | None
    input_tokens: int
    output_tokens: int
    record_count: int


def _validate_time_range(
    start: datetime | None,
    end: datetime | None,
) -> None:
    """Raise ``ValueError`` if *start* >= *end* when both are given."""
    if start is not None and end is not None and start >= end:
        logger.warning(
            BUDGET_TIME_RANGE_INVALID,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
        raise ValueError(msg)


def _filter_records(  # noqa: PLR0913
    records: Sequence[CostRecord],
    *,
    agent_id: NotBlankStr | None = None,
    task_id: NotBlankStr | None = None,
    project_id: NotBlankStr | None = None,
    provider: NotBlankStr | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[CostRecord, ...]:
    """Filter records by agent, task, project, provider, and/or time range.

    Time semantics: ``start <= timestamp < end``.
    """
    return tuple(
        r
        for r in records
        if (agent_id is None or r.agent_id == agent_id)
        and (task_id is None or r.task_id == task_id)
        and (project_id is None or r.project_id == project_id)
        and (provider is None or r.provider == provider)
        and (start is None or r.timestamp >= start)
        and (end is None or r.timestamp < end)
    )


def _aggregate(
    records: Sequence[CostRecord],
    *,
    agent_id: NotBlankStr | None = None,
    task_id: NotBlankStr | None = None,
    project_id: NotBlankStr | None = None,
) -> _AggregateResult:
    """Aggregate records into cost, token totals, count, and currency.

    Same-currency invariant: every contributing record must share the
    same ``currency``.  Mixed currencies raise
    :class:`MixedCurrencyAggregationError` before any summation runs,
    so callers cannot accidentally produce a cost in an undefined unit.
    """
    currency = assert_currencies_match(
        (r.currency for r in records),
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
    )
    cost = sum_cost(records)
    input_tokens = sum(r.input_tokens for r in records)
    output_tokens = sum(r.output_tokens for r in records)
    return _AggregateResult(
        cost=cost,
        currency=currency,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        record_count=len(records),
    )


def _build_agent_spendings(
    filtered: Sequence[CostRecord],
) -> list[AgentSpending]:
    """Group filtered records by agent and aggregate each group."""
    by_agent = group_by_agent(filtered)
    result: list[AgentSpending] = []
    for aid in sorted(by_agent):
        agg = _aggregate(by_agent[aid], agent_id=aid)
        result.append(
            AgentSpending(
                agent_id=aid,
                total_cost=agg.cost,
                currency=agg.currency,
                total_input_tokens=agg.input_tokens,
                total_output_tokens=agg.output_tokens,
                record_count=agg.record_count,
            )
        )
    return result
