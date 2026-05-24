"""Performance signal aggregator.

Wraps the PerformanceTracker to produce an OrgPerformanceSummary
with org-wide quality, success rate, collaboration scores, and
per-window metric summaries across all configured rolling windows.
"""

import re
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    MetricSummary,
    OrgPerformanceSummary,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)

_EMPTY = OrgPerformanceSummary(
    avg_quality_score=0.0,
    avg_success_rate=0.0,
    avg_collaboration_score=0.0,
    agent_count=0,
)

_WINDOW_DAYS_RE = re.compile(r"(\d+)d")


def _parse_window_days(window_size: str) -> int | None:
    """Extract days from a window size string like '7d', '30d'."""
    m = _WINDOW_DAYS_RE.match(window_size)
    return int(m.group(1)) if m else None


def _build_window_metrics(
    window_success: dict[str, list[float]],
    window_quality: dict[str, list[float]],
) -> tuple[list[MetricSummary], float]:
    """Build per-window MetricSummary entries.

    Args:
        window_success: Per-window success rate values.
        window_quality: Per-window quality score values.

    Returns:
        Tuple of (metrics list, average success rate across all windows).
    """
    metrics: list[MetricSummary] = []
    all_success: list[float] = []
    for ws, rates in sorted(window_success.items()):
        days = _parse_window_days(ws) or 7
        avg = round(sum(rates) / len(rates), 4)
        metrics.append(
            MetricSummary(
                name=f"success_rate_{ws}",
                value=avg,
                window_days=days,
            )
        )
        all_success.extend(rates)
    for ws, scores in sorted(window_quality.items()):
        days = _parse_window_days(ws) or 7
        avg = round(sum(scores) / len(scores), 4)
        metrics.append(
            MetricSummary(
                name=f"quality_{ws}",
                value=avg,
                window_days=days,
            )
        )
    avg_success = round(sum(all_success) / len(all_success), 4) if all_success else 0.0
    return metrics, avg_success


class PerformanceSignalAggregator:
    """Aggregates per-agent performance into org-wide summaries.

    Iterates all configured rolling windows (7d, 30d, 90d, etc.)
    and produces per-window MetricSummary entries for quality,
    success rate, and collaboration.

    Args:
        tracker: The PerformanceTracker service instance.
        agent_ids_provider: Callable returning current active agent IDs.
    """

    def __init__(
        self,
        *,
        tracker: PerformanceTracker,
        agent_ids_provider: object,
    ) -> None:
        self._tracker = tracker
        self._agent_ids_provider = agent_ids_provider

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name."""
        return NotBlankStr("performance")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgPerformanceSummary:
        """Aggregate org-wide performance from individual snapshots.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide performance summary with per-window metrics.
        """
        _ = since  # Will be used for windowed filtering.
        try:
            agent_ids = self._get_agent_ids()
            if not agent_ids:
                return _EMPTY

            quality_scores: list[float] = []
            collab_scores: list[float] = []
            # Per-window accumulators: window_size -> list of values.
            window_success: dict[str, list[float]] = {}
            window_quality: dict[str, list[float]] = {}

            for agent_id in agent_ids:
                snapshot = await self._tracker.get_snapshot(agent_id, now=until)
                q = snapshot.overall_quality_score
                if q is not None:
                    quality_scores.append(q)
                c = snapshot.overall_collaboration_score
                if c is not None:
                    collab_scores.append(c)
                for window in snapshot.windows:
                    ws = window.window_size
                    if window.success_rate is not None:
                        window_success.setdefault(ws, []).append(window.success_rate)
                    if window.avg_quality_score is not None:
                        window_quality.setdefault(ws, []).append(
                            window.avg_quality_score
                        )

            avg_quality = (
                round(sum(quality_scores) / len(quality_scores), 4)
                if quality_scores
                else 0.0
            )
            avg_collab = (
                round(sum(collab_scores) / len(collab_scores), 4)
                if collab_scores
                else 0.0
            )

            metrics, avg_success = _build_window_metrics(window_success, window_quality)

            summary = OrgPerformanceSummary(
                avg_quality_score=min(avg_quality, 10.0),
                avg_success_rate=min(avg_success, 1.0),
                avg_collaboration_score=min(avg_collab, 10.0),
                metrics=tuple(metrics),
                agent_count=len(agent_ids),
            )

            logger.info(
                META_SIGNAL_AGGREGATION_COMPLETED,
                domain="performance",
                agent_count=len(agent_ids),
                avg_quality=avg_quality,
                windows=len(metrics),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="performance"
            )
            return _EMPTY
        else:
            return summary

    def _get_agent_ids(self) -> tuple[str, ...]:
        """Get current active agent IDs from the provider."""
        if callable(self._agent_ids_provider):
            result = self._agent_ids_provider()
            if isinstance(result, (list, tuple)):
                return tuple(str(a) for a in result)
        return ()
