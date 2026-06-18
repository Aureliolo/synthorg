# module-kind: code
"""Shared cost-record aggregation helpers.

Both backend cost-record repositories run the same single-snapshot
aggregating query (``COUNT(DISTINCT)`` + concat + ``SUM``) and then apply
identical row handling: reject a missing row, reject mixed currencies, and
return the summed total. That post-query logic lives here once so the two
backends cannot drift on what counts as a mixed-currency rejection.
"""

from collections.abc import Sequence
from typing import cast

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.core.normalization import parse_comma_list
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger
from synthorg.observability.events.persistence.cost_record import (
    PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
    PERSISTENCE_COST_RECORD_AGGREGATED,
)
from synthorg.persistence._shared import safe_float, safe_int

logger = get_logger(__name__)


def resolve_currency_aggregate(
    row: Sequence[object] | None,
    *,
    agent_id: str | None,
    task_id: str | None,
) -> float:
    """Validate an aggregate row and return its summed total cost.

    The row must carry ``(distinct_currency_count, currencies_csv,
    total_cost)`` in that order, matching the shared aggregating query.

    Args:
        row: The single aggregate row, or ``None`` when the query
            returned nothing.
        agent_id: Optional agent filter, for logging.
        task_id: Optional task filter, for logging.

    Returns:
        The summed total cost across the matched rows.

    Raises:
        QueryError: If *row* is ``None``.
        MixedCurrencyAggregationError: If the matched rows span more than
            one currency.
    """
    if row is None:
        msg = "aggregate query returned no rows"
        logger.error(
            PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error=msg,
        )
        raise QueryError(msg)

    distinct_count = safe_int(row[0], default=0)
    currencies_csv = cast("str | None", row[1])
    total = safe_float(row[2], default=0.0)
    if distinct_count > 1:
        distinct = frozenset(parse_comma_list(currencies_csv))
        logger.error(
            PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            currencies=sorted(distinct),
            error="mixed-currency aggregation rejected",
        )
        mixed_msg = "Cannot aggregate costs across mixed currencies"
        raise MixedCurrencyAggregationError(
            mixed_msg,
            currencies=distinct,
            agent_id=agent_id,
            task_id=task_id,
        )
    logger.debug(
        PERSISTENCE_COST_RECORD_AGGREGATED,
        agent_id=agent_id,
        total_cost=total,
    )
    return total
