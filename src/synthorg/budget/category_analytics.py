"""Category-based analytics for LLM call cost breakdown.

Provides pure functions to build per-category cost breakdowns and
compute orchestration overhead ratios from cost records tagged with
:class:`~synthorg.budget.call_category.LLMCallCategory`.
"""

import math
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.call_category import (
    LLMCallCategory,
    OrchestrationAlertLevel,
)
from synthorg.budget.coordination_config import (
    OrchestrationAlertThresholds,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.budget.cost_record import CostRecord

logger = get_logger(__name__)


class CategoryBreakdown(BaseModel):
    """Per-category cost, token, and count breakdown.

    Attributes:
        productive_cost: Total cost for productive calls.
        productive_tokens: Total tokens for productive calls.
        productive_count: Number of productive calls.
        coordination_cost: Total cost for coordination calls.
        coordination_tokens: Total tokens for coordination calls.
        coordination_count: Number of coordination calls.
        system_cost: Total cost for system calls.
        system_tokens: Total tokens for system calls.
        system_count: Number of system calls.
        uncategorized_cost: Total cost for uncategorized calls.
        uncategorized_tokens: Total tokens for uncategorized calls.
        uncategorized_count: Number of uncategorized calls.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    productive_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Productive call cost",
    )
    productive_tokens: int = Field(
        default=0,
        ge=0,
        description="Productive call tokens",
    )
    productive_count: int = Field(
        default=0,
        ge=0,
        description="Productive call count",
    )
    coordination_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Coordination call cost",
    )
    coordination_tokens: int = Field(
        default=0,
        ge=0,
        description="Coordination call tokens",
    )
    coordination_count: int = Field(
        default=0,
        ge=0,
        description="Coordination call count",
    )
    system_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="System call cost",
    )
    system_tokens: int = Field(
        default=0,
        ge=0,
        description="System call tokens",
    )
    system_count: int = Field(
        default=0,
        ge=0,
        description="System call count",
    )
    uncategorized_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Uncategorized call cost",
    )
    uncategorized_tokens: int = Field(
        default=0,
        ge=0,
        description="Uncategorized call tokens",
    )
    uncategorized_count: int = Field(
        default=0,
        ge=0,
        description="Uncategorized call count",
    )


class OrchestrationRatio(BaseModel):
    """Orchestration overhead ratio and alert level.

    The ratio measures the fraction of non-productive (coordination +
    system) tokens relative to total tokens.

    Attributes:
        ratio: Orchestration ratio (0.0-1.0).
        alert_level: Alert level based on ratio thresholds.
        total_tokens: Total tokens across all categories (includes
            uncategorized tokens in the denominator).
        productive_tokens: Productive category tokens.
        coordination_tokens: Coordination category tokens.
        system_tokens: System category tokens.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    ratio: float = Field(ge=0.0, le=1.0, description="Orchestration ratio")
    alert_level: OrchestrationAlertLevel = Field(
        description="Alert level for orchestration overhead",
    )
    total_tokens: int = Field(ge=0, description="Total tokens")
    productive_tokens: int = Field(ge=0, description="Productive tokens")
    coordination_tokens: int = Field(ge=0, description="Coordination tokens")
    system_tokens: int = Field(ge=0, description="System tokens")

    @model_validator(mode="after")
    def _validate_token_consistency(self) -> Self:
        """Ensure total_tokens >= sum of category tokens."""
        category_sum = (
            self.productive_tokens + self.coordination_tokens + self.system_tokens
        )
        if self.total_tokens < category_sum:
            msg = (
                f"total_tokens ({self.total_tokens}) must be >= "
                f"sum of category tokens ({category_sum})"
            )
            raise ValueError(msg)
        return self


def build_category_breakdown(
    records: Sequence[CostRecord],
) -> CategoryBreakdown:
    """Build a per-category cost/token breakdown from cost records.

    Records without a ``call_category`` are counted as uncategorized.
    Uses :func:`math.fsum` for accurate floating-point summation.
    """
    buckets: dict[LLMCallCategory | None, tuple[list[float], int, int]] = {
        cat: ([], 0, 0) for cat in LLMCallCategory
    } | {None: ([], 0, 0)}

    for r in records:
        bucket_key = r.call_category if r.call_category in buckets else None
        costs, tokens, count = buckets[bucket_key]
        costs.append(r.cost_usd)
        # Integer accumulators are in a tuple; replace the tuple to
        # update them (the costs list is mutated in-place).
        buckets[bucket_key] = (
            costs,
            tokens + r.input_tokens + r.output_tokens,
            count + 1,
        )

    def _round(vals: list[float]) -> float:
        return round(math.fsum(vals), BUDGET_ROUNDING_PRECISION)

    p = buckets[LLMCallCategory.PRODUCTIVE]
    c = buckets[LLMCallCategory.COORDINATION]
    s = buckets[LLMCallCategory.SYSTEM]
    u = buckets[None]

    return CategoryBreakdown(
        productive_cost=_round(p[0]),
        productive_tokens=p[1],
        productive_count=p[2],
        coordination_cost=_round(c[0]),
        coordination_tokens=c[1],
        coordination_count=c[2],
        system_cost=_round(s[0]),
        system_tokens=s[1],
        system_count=s[2],
        uncategorized_cost=_round(u[0]),
        uncategorized_tokens=u[1],
        uncategorized_count=u[2],
    )


def compute_orchestration_ratio(
    breakdown: CategoryBreakdown,
    *,
    thresholds: OrchestrationAlertThresholds | None = None,
) -> OrchestrationRatio:
    """Compute the orchestration overhead ratio from a category breakdown.

    The ratio is ``(coordination_tokens + system_tokens) / total_tokens``.
    When total tokens is zero, the ratio is ``0.0`` with ``NORMAL`` alert.

    Args:
        breakdown: Per-category cost breakdown.
        thresholds: Optional custom alert thresholds.  Defaults to
            ``OrchestrationAlertThresholds()`` (30/50/70%).
    """
    if thresholds is None:
        thresholds = OrchestrationAlertThresholds()

    total = (
        breakdown.productive_tokens
        + breakdown.coordination_tokens
        + breakdown.system_tokens
        + breakdown.uncategorized_tokens
    )

    if total == 0:
        return OrchestrationRatio(
            ratio=0.0,
            alert_level=OrchestrationAlertLevel.NORMAL,
            total_tokens=0,
            productive_tokens=0,
            coordination_tokens=0,
            system_tokens=0,
        )

    overhead = breakdown.coordination_tokens + breakdown.system_tokens
    ratio = overhead / total

    alert = _ratio_to_alert(ratio, thresholds)

    return OrchestrationRatio(
        ratio=round(ratio, BUDGET_ROUNDING_PRECISION),
        alert_level=alert,
        total_tokens=total,
        productive_tokens=breakdown.productive_tokens,
        coordination_tokens=breakdown.coordination_tokens,
        system_tokens=breakdown.system_tokens,
    )


def _ratio_to_alert(
    ratio: float,
    thresholds: OrchestrationAlertThresholds,
) -> OrchestrationAlertLevel:
    """Map a ratio to an alert level using the given thresholds."""
    if ratio >= thresholds.critical:
        return OrchestrationAlertLevel.CRITICAL
    if ratio >= thresholds.warn:
        return OrchestrationAlertLevel.WARNING
    if ratio >= thresholds.info:
        return OrchestrationAlertLevel.INFO
    return OrchestrationAlertLevel.NORMAL
