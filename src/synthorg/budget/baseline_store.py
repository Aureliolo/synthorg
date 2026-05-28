"""Single-agent baseline data store for coordination metrics.

Maintains a sliding window of single-agent execution records used
to compute comparison baselines for coordination efficiency (Ec),
overhead (O%), and error amplification (Ae) metrics.
"""

import statistics
from collections import deque
from datetime import UTC, datetime
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.coordination_metrics import (
    COORD_METRICS_BASELINE_INSUFFICIENT,
    COORD_METRICS_BASELINE_RECORDED,
)

logger = get_logger(__name__)

# Default rolling-window size for the single-agent baseline store. Sized
# for ~1 day of normal traffic on a small org while keeping in-memory
# state bounded.
_DEFAULT_BASELINE_WINDOW_SIZE: Final[int] = 50


class BaselineRecord(BaseModel):
    """Single-agent execution record for baseline computation.

    Attributes:
        agent_id: Executing agent identifier.
        task_id: Task identifier.
        turns: Number of LLM turns used.
        error_rate: Fraction of turns that ended in error (0.0-1.0).
        total_tokens: Total tokens consumed.
        duration_seconds: Wall-clock execution time.
        timestamp: When the record was captured (UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Executing agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    turns: float = Field(gt=0, description="Number of LLM turns")
    error_rate: float = Field(ge=0.0, le=1.0, description="Error fraction (0-1)")
    total_tokens: float = Field(ge=0.0, description="Total tokens consumed")
    duration_seconds: float = Field(ge=0.0, description="Wall-clock execution time")
    timestamp: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Capture timestamp (UTC)",
    )


class BaselineStore:
    """Sliding-window store for single-agent baseline data.

    Records from single-agent executions are stored up to
    ``window_size``. Older records are evicted automatically when the
    window is full. The store is not thread-safe; it assumes
    single-loop asyncio concurrency (same as CostTracker).

    Args:
        window_size: Maximum number of records to retain.
    """

    def __init__(self, *, window_size: int = _DEFAULT_BASELINE_WINDOW_SIZE) -> None:
        if window_size <= 0:
            msg = "window_size must be positive"
            raise ValueError(msg)
        self._records: deque[BaselineRecord] = deque(maxlen=window_size)
        self._window_size = window_size

    def __len__(self) -> int:
        """Number of records currently in the store.

        Returns:
            Result of type ``int``.
        """
        return len(self._records)

    def record(self, baseline: BaselineRecord) -> None:
        """Append a single-agent baseline record.

        If the window is full the oldest record is evicted
        automatically by the underlying ``deque``.

        Args:
            baseline: Baseline record to store.
        """
        self._records.append(baseline)
        logger.info(
            COORD_METRICS_BASELINE_RECORDED,
            agent_id=baseline.agent_id,
            task_id=baseline.task_id,
            turns=baseline.turns,
            store_size=len(self._records),
            window_size=self._window_size,
        )

    def get_baseline_turns(self) -> float | None:
        """Mean turns across stored records, or None if empty.

        Returns:
            The matching ``float``, or ``None`` when no match is found.
        """
        if not self._records:
            logger.debug(COORD_METRICS_BASELINE_INSUFFICIENT, metric="turns")
            return None
        return statistics.mean(r.turns for r in self._records)

    def get_baseline_error_rate(self) -> float | None:
        """Mean error rate across stored records, or None if empty.

        Returns:
            The matching ``float``, or ``None`` when no match is found.
        """
        if not self._records:
            logger.debug(COORD_METRICS_BASELINE_INSUFFICIENT, metric="error_rate")
            return None
        return statistics.mean(r.error_rate for r in self._records)

    def get_baseline_tokens(self) -> float | None:
        """Mean total tokens across stored records, or None if empty.

        Returns:
            The matching ``float``, or ``None`` when no match is found.
        """
        if not self._records:
            logger.debug(COORD_METRICS_BASELINE_INSUFFICIENT, metric="total_tokens")
            return None
        return statistics.mean(r.total_tokens for r in self._records)

    def get_baseline_duration(self) -> float | None:
        """Mean duration in seconds across stored records, or None if empty.

        Returns:
            The matching ``float``, or ``None`` when no match is found.
        """
        if not self._records:
            logger.debug(COORD_METRICS_BASELINE_INSUFFICIENT, metric="duration_seconds")
            return None
        return statistics.mean(r.duration_seconds for r in self._records)
