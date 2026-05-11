"""Cumulative risk tracking service.

Provides an in-memory store with TTL-based eviction for
:class:`RiskRecord` entries and aggregation queries consumed by the
budget enforcer and risk monitoring.

Service layer for the Risk Budget section of the Operations design page.
The implementation mirrors :class:`CostTracker` for consistency.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger
from synthorg.observability.events.risk_budget import (
    RISK_BUDGET_AGENT_QUERIED,
    RISK_BUDGET_RECORD_ADDED,
    RISK_BUDGET_RECORDS_AUTO_PRUNED,
    RISK_BUDGET_RECORDS_PRUNED,
    RISK_BUDGET_RECORDS_QUERIED,
    RISK_BUDGET_TASK_QUERIED,
    RISK_BUDGET_TOTAL_QUERIED,
    RISK_BUDGET_TRACKER_CREATED,
)

if TYPE_CHECKING:
    from synthorg.budget.risk_config import RiskBudgetConfig
    from synthorg.budget.risk_record import RiskRecord

logger = get_logger(__name__)

_RISK_WINDOW_HOURS: Final[int] = 168  # 7 days
_AUTO_PRUNE_THRESHOLD: Final[int] = 100_000


class RiskTracker:
    """In-memory risk tracking service with TTL-based eviction.

    Records :class:`RiskRecord` entries from agent actions and provides
    aggregation queries for risk budget monitoring.  Memory is bounded
    by a soft TTL-based auto-prune: when the record count exceeds
    *auto_prune_threshold*, records older than 168 hours (7 days) are
    removed on the next query.

    Args:
        risk_budget_config: Optional risk budget configuration.
        auto_prune_threshold: Maximum record count before auto-pruning
            is triggered on snapshot.  Defaults to 100,000.

    Raises:
        ValueError: If *auto_prune_threshold* < 1.
    """

    def __init__(
        self,
        *,
        risk_budget_config: RiskBudgetConfig | None = None,
        auto_prune_threshold: int = _AUTO_PRUNE_THRESHOLD,
    ) -> None:
        if auto_prune_threshold < 1:
            msg = f"auto_prune_threshold must be >= 1, got {auto_prune_threshold}"
            raise ValueError(msg)
        self._records: list[RiskRecord] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._risk_budget_config = risk_budget_config
        self._auto_prune_threshold = auto_prune_threshold
        logger.debug(
            RISK_BUDGET_TRACKER_CREATED,
            has_config=risk_budget_config is not None,
        )

    @property
    def risk_budget_config(self) -> RiskBudgetConfig | None:
        """The optional risk budget configuration."""
        return self._risk_budget_config

    async def record(self, risk_record: RiskRecord) -> None:
        """Append a risk record.

        Args:
            risk_record: Immutable risk record to store.
        """
        async with self._lock:
            self._records.append(risk_record)
            logger.info(
                RISK_BUDGET_RECORD_ADDED,
                agent_id=risk_record.agent_id,
                action_type=risk_record.action_type,
                risk_units=risk_record.risk_units,
            )

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        """Remove records older than the 168-hour (7-day) window.

        Args:
            now: Reference time.  Defaults to current UTC time.

        Returns:
            Number of records removed.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=_RISK_WINDOW_HOURS)
        async with self._lock:
            pruned = self._prune_before(cutoff)
            if pruned:
                logger.info(
                    RISK_BUDGET_RECORDS_PRUNED,
                    pruned=pruned,
                    remaining=len(self._records),
                )
            return pruned

    async def get_total_risk(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``risk_units`` across all records.

        Args:
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Total risk units.

        Raises:
            ValueError: If ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(RISK_BUDGET_TOTAL_QUERIED, start=start, end=end)
        snapshot = await self._snapshot()
        filtered = _filter_records(snapshot, start=start, end=end)
        return _sum_risk_units(filtered)

    async def get_agent_risk(
        self,
        agent_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``risk_units`` for a single agent.

        Args:
            agent_id: Agent identifier to filter by.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Total risk units for the agent.

        Raises:
            ValueError: If ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            RISK_BUDGET_AGENT_QUERIED,
            agent_id=agent_id,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        filtered = _filter_records(
            snapshot,
            agent_id=agent_id,
            start=start,
            end=end,
        )
        return _sum_risk_units(filtered)

    async def get_task_risk(
        self,
        task_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``risk_units`` for a single task.

        Args:
            task_id: Task identifier to filter by.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Total risk units for the task.

        Raises:
            ValueError: If ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            RISK_BUDGET_TASK_QUERIED,
            task_id=task_id,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        filtered = _filter_records(
            snapshot,
            task_id=task_id,
            start=start,
            end=end,
        )
        return _sum_risk_units(filtered)

    async def get_records(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        action_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[RiskRecord, ...]:
        """Return filtered risk records as an immutable tuple.

        Args:
            agent_id: Filter by agent.
            task_id: Filter by task.
            action_type: Filter by action type.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Immutable tuple of matching records.

        Raises:
            ValueError: If ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            RISK_BUDGET_RECORDS_QUERIED,
            agent_id=agent_id,
            task_id=task_id,
            action_type=action_type,
        )
        snapshot = await self._snapshot()
        filtered = _filter_records(
            snapshot,
            agent_id=agent_id,
            task_id=task_id,
            action_type=action_type,
            start=start,
            end=end,
        )
        return tuple(filtered)

    async def get_record_count(self) -> int:
        """Total number of recorded risk entries."""
        async with self._lock:
            return len(self._records)

    # ── Internal helpers ─────────────────────────────────────────

    async def _snapshot(self) -> list[RiskRecord]:
        """Return a shallow copy of records, auto-pruning if needed."""
        async with self._lock:
            if len(self._records) > self._auto_prune_threshold:
                cutoff = datetime.now(UTC) - timedelta(
                    hours=_RISK_WINDOW_HOURS,
                )
                pruned = self._prune_before(cutoff)
                if pruned:
                    logger.info(
                        RISK_BUDGET_RECORDS_AUTO_PRUNED,
                        pruned=pruned,
                        remaining=len(self._records),
                    )
            return list(self._records)

    def _prune_before(self, cutoff: datetime) -> int:
        """Remove records with timestamp before cutoff.  Caller holds lock."""
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        return before - len(self._records)


# ── Module-level pure helpers ────────────────────────────────────


def _validate_time_range(
    start: datetime | None,
    end: datetime | None,
) -> None:
    """Raise ValueError if start >= end."""
    if start is not None and end is not None and start >= end:
        msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
        raise ValueError(msg)


def _filter_records(  # noqa: PLR0913
    records: list[RiskRecord],
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    action_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[RiskRecord]:
    """Filter records by optional criteria."""
    gen = (r for r in records)
    if agent_id is not None:
        gen = (r for r in gen if r.agent_id == agent_id)
    if task_id is not None:
        gen = (r for r in gen if r.task_id == task_id)
    if action_type is not None:
        gen = (r for r in gen if r.action_type == action_type)
    if start is not None:
        gen = (r for r in gen if r.timestamp >= start)
    if end is not None:
        gen = (r for r in gen if r.timestamp < end)
    return list(gen)


def _sum_risk_units(records: list[RiskRecord]) -> float:
    """Sum risk_units from a list of records using fsum for precision."""
    return math.fsum(r.risk_units for r in records)
