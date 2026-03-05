"""Real-time cost tracking service.

Provides an append-only in-memory store for :class:`CostRecord` entries and
aggregation queries consumed by the CFO agent and budget monitoring.

Implements DESIGN_SPEC Section 10.2 service layer.  Persistence (SQLite) is
deferred to M5; the current implementation is purely in-memory.
"""

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from ai_company.budget.enums import BudgetAlertLevel
from ai_company.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)
from ai_company.constants import BUDGET_ROUNDING_PRECISION
from ai_company.observability import get_logger
from ai_company.observability.events import (
    BUDGET_DEPARTMENT_RESOLVE_FAILED,
    BUDGET_RECORD_ADDED,
    BUDGET_SUMMARY_BUILT,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from ai_company.budget.config import BudgetConfig
    from ai_company.budget.cost_record import CostRecord

logger = get_logger(__name__)


class CostTracker:
    """In-memory, append-only cost tracking service.

    Records :class:`CostRecord` entries from LLM API calls and provides
    aggregation queries for budget monitoring.

    Args:
        budget_config: Optional budget configuration for alert level
            computation.  When ``None``, alert level defaults to
            ``NORMAL`` and ``budget_used_percent`` to ``0.0``.
        department_resolver: Optional callable mapping ``agent_id`` to a
            department name.  When ``None`` or returning ``None`` for an
            agent, the agent is excluded from department aggregation.
    """

    def __init__(
        self,
        *,
        budget_config: BudgetConfig | None = None,
        department_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._records: list[CostRecord] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._budget_config = budget_config
        self._department_resolver = department_resolver

    @property
    def record_count(self) -> int:
        """Total number of recorded cost entries (lock-free)."""
        return len(self._records)

    @property
    def total_cost_usd(self) -> float:
        """Total cost across all records (lock-free)."""
        return round(
            sum(r.cost_usd for r in self._records),
            BUDGET_ROUNDING_PRECISION,
        )

    async def record(self, cost_record: CostRecord) -> None:
        """Append a cost record.

        Args:
            cost_record: Immutable cost record to store.
        """
        async with self._lock:
            self._records.append(cost_record)
        logger.info(
            BUDGET_RECORD_ADDED,
            agent_id=cost_record.agent_id,
            model=cost_record.model,
            cost_usd=cost_record.cost_usd,
        )

    async def get_total_cost(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``cost_usd`` across all records, optionally filtered by time.

        Args:
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Rounded total cost in USD.
        """
        snapshot = await self._snapshot()
        filtered = _filter_records(snapshot, start=start, end=end)
        cost, _, _, _ = _aggregate(filtered)
        return cost

    async def get_agent_cost(
        self,
        agent_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``cost_usd`` for a single agent, optionally filtered by time.

        Args:
            agent_id: Agent identifier to filter by.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Rounded total cost in USD for the agent.
        """
        snapshot = await self._snapshot()
        filtered = _filter_records(snapshot, agent_id=agent_id, start=start, end=end)
        cost, _, _, _ = _aggregate(filtered)
        return cost

    async def get_record_count(self) -> int:
        """Total number of recorded cost entries (async, lock-safe).

        Returns:
            Number of cost records.
        """
        async with self._lock:
            return len(self._records)

    async def build_summary(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> SpendingSummary:
        """Build a full spending summary for the given period.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.

        Returns:
            Aggregated spending summary with per-agent and per-department
            breakdowns, budget utilisation, and alert level.
        """
        snapshot = await self._snapshot()
        filtered = _filter_records(snapshot, start=start, end=end)
        total_cost, total_in, total_out, count = _aggregate(filtered)

        # Per-agent aggregation
        by_agent_map: dict[str, list[CostRecord]] = defaultdict(list)
        for rec in filtered:
            by_agent_map[rec.agent_id].append(rec)

        agent_spendings: list[AgentSpending] = []
        for aid in sorted(by_agent_map):
            a_cost, a_in, a_out, a_count = _aggregate(by_agent_map[aid])
            agent_spendings.append(
                AgentSpending(
                    agent_id=aid,
                    total_cost_usd=a_cost,
                    total_input_tokens=a_in,
                    total_output_tokens=a_out,
                    record_count=a_count,
                )
            )

        # Per-department aggregation
        dept_map: dict[str, list[CostRecord]] = defaultdict(list)
        for aid, records in by_agent_map.items():
            dept = self._resolve_department(aid)
            if dept is not None:
                dept_map[dept].extend(records)

        dept_spendings: list[DepartmentSpending] = []
        for dname in sorted(dept_map):
            d_cost, d_in, d_out, d_count = _aggregate(dept_map[dname])
            dept_spendings.append(
                DepartmentSpending(
                    department_name=dname,
                    total_cost_usd=d_cost,
                    total_input_tokens=d_in,
                    total_output_tokens=d_out,
                    record_count=d_count,
                )
            )

        # Budget context
        budget_monthly = (
            self._budget_config.total_monthly if self._budget_config else 0.0
        )
        used_pct = (
            round(total_cost / budget_monthly * 100, BUDGET_ROUNDING_PRECISION)
            if budget_monthly > 0
            else 0.0
        )
        alert = self._compute_alert_level(total_cost)

        summary = SpendingSummary(
            period=PeriodSpending(
                start=start,
                end=end,
                total_cost_usd=total_cost,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
                record_count=count,
            ),
            by_agent=tuple(agent_spendings),
            by_department=tuple(dept_spendings),
            budget_total_monthly=budget_monthly,
            budget_used_percent=used_pct,
            alert_level=alert,
        )

        logger.info(
            BUDGET_SUMMARY_BUILT,
            total_cost_usd=total_cost,
            record_count=count,
            agent_count=len(agent_spendings),
            department_count=len(dept_spendings),
            alert_level=alert.value,
        )

        return summary

    # ── Private helpers ──────────────────────────────────────────────

    async def _snapshot(self) -> tuple[CostRecord, ...]:
        """Acquire lock, copy records, release lock."""
        async with self._lock:
            return tuple(self._records)

    def _compute_alert_level(self, total_cost: float) -> BudgetAlertLevel:
        """Determine alert level from total cost and budget config."""
        if self._budget_config is None or self._budget_config.total_monthly <= 0:
            return BudgetAlertLevel.NORMAL

        pct = total_cost / self._budget_config.total_monthly * 100
        alerts = self._budget_config.alerts

        if pct >= alerts.hard_stop_at:
            return BudgetAlertLevel.HARD_STOP
        if pct >= alerts.critical_at:
            return BudgetAlertLevel.CRITICAL
        if pct >= alerts.warn_at:
            return BudgetAlertLevel.WARNING
        return BudgetAlertLevel.NORMAL

    def _resolve_department(self, agent_id: str) -> str | None:
        """Resolve agent to department, swallowing resolver errors."""
        if self._department_resolver is None:
            return None
        try:
            return self._department_resolver(agent_id)
        except Exception:
            logger.warning(
                BUDGET_DEPARTMENT_RESOLVE_FAILED,
                agent_id=agent_id,
            )
            return None


# ── Module-level pure helpers ────────────────────────────────────


def _filter_records(
    records: Sequence[CostRecord],
    *,
    agent_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[CostRecord, ...]:
    """Filter records by agent and/or time range.

    Time semantics: ``start <= timestamp < end``.
    """
    result = records
    if agent_id is not None:
        result = [r for r in result if r.agent_id == agent_id]
    if start is not None:
        result = [r for r in result if r.timestamp >= start]
    if end is not None:
        result = [r for r in result if r.timestamp < end]
    return tuple(result)


def _aggregate(
    records: Sequence[CostRecord],
) -> tuple[float, int, int, int]:
    """Aggregate records into (cost, input_tokens, output_tokens, count)."""
    cost = round(
        sum(r.cost_usd for r in records),
        BUDGET_ROUNDING_PRECISION,
    )
    input_tokens = sum(r.input_tokens for r in records)
    output_tokens = sum(r.output_tokens for r in records)
    return cost, input_tokens, output_tokens, len(records)
