"""In-memory store for coordination metrics records.

Stores timestamped :class:`CoordinationMetrics` snapshots from
completed multi-agent coordination runs and supports filtered
queries for the ``GET /coordination/metrics`` endpoint.
"""

from collections import deque
from datetime import datetime
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.budget.coordination_metric_models import CoordinationMetrics
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.coordination_metrics import (
    COORD_METRICS_COLLECTION_COMPLETED,
    COORD_METRICS_STORE_CLEARED,
    COORD_METRICS_VALIDATION_ERROR,
)

logger = get_logger(__name__)
_DEFAULT_MAX_ENTRIES: Final[int] = 10000
_DEFAULT_LIMIT: Final[int] = 10000


class CoordinationMetricsRecord(BaseModel):
    """Timestamped coordination metrics from a single run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="Associated task")
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Lead agent (None for system-level runs)",
    )
    computed_at: AwareDatetime = Field(
        description="When metrics were collected",
    )
    team_size: int = Field(gt=0, description="Coordinating agents")
    metrics: CoordinationMetrics = Field(
        description="All nine Kim et al. coordination metrics",
    )


class CoordinationMetricsStore:
    """In-memory ring-buffer store for coordination metrics.

    Follows the same eviction pattern as
    :class:`~synthorg.security.audit.AuditLog`.

    Args:
        max_entries: Maximum records retained (oldest evicted first).

    Raises:
        ValueError: If *max_entries* < 1.
    """

    def __init__(self, *, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            msg = f"max_entries must be >= 1, got {max_entries}"
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        self._max_entries = max_entries
        self._records: deque[CoordinationMetricsRecord] = deque(
            maxlen=max_entries,
        )

    def clear(self) -> None:
        """Reset all coordination metrics for test isolation."""
        cleared_count = len(self._records)
        self._records.clear()
        logger.info(COORD_METRICS_STORE_CLEARED, cleared_count=cleared_count)

    def record(self, entry: CoordinationMetricsRecord) -> None:
        """Append a metrics record (oldest evicted when full)."""
        self._records.append(entry)
        logger.info(
            COORD_METRICS_COLLECTION_COMPLETED,
            task_id=entry.task_id,
            agent_id=entry.agent_id,
            team_size=entry.team_size,
        )

    def query(
        self,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[tuple[CoordinationMetricsRecord, ...], int]:
        """Query records with optional AND-combined filters.

        Args:
            task_id: Filter by task identifier.
            agent_id: Filter by lead agent identifier.
            since: Exclude records before this datetime.
            until: Exclude records after this datetime.
            limit: Maximum results to return (must be >= 1).

        Returns:
            A ``(records, total_matches)`` pair.  *records* contains
            up to *limit* newest-first entries; *total_matches* is
            the true count of all matching records (may exceed
            ``len(records)`` when capped by *limit*).

        Raises:
            ValueError: If *limit* < 1 or *since* > *until*.
        """
        if since is not None and until is not None and since > until:
            msg = "since must not be after until"
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        results: list[CoordinationMetricsRecord] = []
        total_matches = 0
        for rec in reversed(self._records):
            if task_id is not None and rec.task_id != task_id:
                continue
            if agent_id is not None and rec.agent_id != agent_id:
                continue
            if since is not None and rec.computed_at < since:
                continue
            if until is not None and rec.computed_at > until:
                continue
            total_matches += 1
            if len(results) < limit:
                results.append(rec)
        return tuple(results), total_matches

    def count(self) -> int:
        """Return the number of stored records.

        Returns:
            Result of type ``int``.
        """
        return len(self._records)
