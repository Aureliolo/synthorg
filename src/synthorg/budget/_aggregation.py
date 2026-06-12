"""Shared aggregation helpers for cost-record analyses.

The ``defaultdict(list)`` plus ``math.fsum`` plus cost-per-1k
aggregation idiom used across ``_tracker_helpers`` and
``_optimizer_helpers`` lives here as pure functions with no I/O.

Same-currency enforcement: :func:`sum_cost` calls
``assert_currencies_match`` itself so it is safe by construction;
callers do not need to guard upstream, though doing so still surfaces
the rejection at a richer scope (with ``agent_id`` / ``task_id`` /
``project_id`` context).
"""

import math
from collections import defaultdict
from collections.abc import Sequence

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import assert_currencies_match
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger

logger = get_logger(__name__)


def group_by_agent(
    records: Sequence[CostRecord],
) -> dict[str, list[CostRecord]]:
    """Group cost records by ``agent_id`` preserving insertion order.

    Returns a plain ``dict`` (not a ``defaultdict``) so callers reading
    a missing key raise ``KeyError`` rather than silently materialising
    an empty list -- a defensive barrier against mutation-on-read
    bugs that would skew aggregations downstream.

    Returns:
        Mapping from ``str`` to ``list[CostRecord]``.
    """
    bucket: dict[str, list[CostRecord]] = defaultdict(list)
    for record in records:
        bucket[record.agent_id].append(record)
    return dict(bucket)


def sum_cost(records: Sequence[CostRecord]) -> float:
    """Sum ``cost`` across records using ``math.fsum`` + rounding.

    ``math.fsum`` avoids accumulated floating-point drift that
    plain ``sum()`` would introduce across long sequences.

    Same-currency invariant: callers must hand records that all share
    one currency.  This function double-checks via
    :func:`~synthorg.budget.currency.assert_currencies_match` so the
    primitive is safe by construction even when the caller forgets;
    mixed input raises :class:`MixedCurrencyAggregationError` (HTTP
    409) before any reduction.

    Returns:
        Result of type ``float``.
    """
    assert_currencies_match(r.currency for r in records)
    return round(
        math.fsum(r.cost for r in records),
        BUDGET_ROUNDING_PRECISION,
    )


def sum_tokens(records: Sequence[CostRecord]) -> int:
    """Sum ``input_tokens + output_tokens`` across records.

    Returns:
        Result of type ``int``.
    """
    return sum(r.input_tokens + r.output_tokens for r in records)


def compute_cost_per_1k(total_cost: float, total_tokens: int) -> float:
    """Return cost per 1000 tokens rounded to the budget precision.

    Returns ``0.0`` when ``total_tokens`` is ``0`` so callers never
    divide by zero on agents that ran without token counts (usually
    error or no-op invocations).

    Returns:
        Result of type ``float``.
    """
    if total_tokens <= 0:
        return 0.0
    return round(
        (total_cost / total_tokens) * 1000.0,
        BUDGET_ROUNDING_PRECISION,
    )
