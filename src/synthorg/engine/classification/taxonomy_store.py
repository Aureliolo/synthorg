"""In-memory error taxonomy store.

Ring-buffered, in-process store for classification results produced by
the detector pipeline.  Implements the
:class:`~synthorg.engine.classification.taxonomy_store_protocol.ErrorTaxonomyStore`
protocol and, by the same surface, the
:class:`~synthorg.engine.classification.protocol.ClassificationSink`
protocol so it can be dropped into the existing pipeline sink list.

The store is the single owner of the error-category roll-up logic:
aggregators at the signals layer call :meth:`summarize` rather than
reimplementing category counting, severity averaging, and trend
detection.
"""

import asyncio
import copy
from collections import deque
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.meta.signal_models import (
    ErrorCategorySummary,
    OrgErrorSummary,
    TrendDirection,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.classification import (
    TAXONOMY_STORE_APPEND_FAILED,
    TAXONOMY_STORE_APPENDED,
    TAXONOMY_STORE_EVICTED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from synthorg.budget.coordination_config import ErrorCategory
    from synthorg.engine.classification.models import (
        ClassificationResult,
        ErrorFinding,
    )

logger = get_logger(__name__)

_DEFAULT_MAX_RESULTS: Final[int] = 10_000
"""Default ring-buffer capacity.

At ~50 results per minute under heavy load this covers the last 3.5
hours, which is more than enough for the 7-day signals window since
typical traffic is much lower.  Operators can raise this through the
constructor if they need longer retention from the in-memory store;
a durable backend is the right answer for multi-day retention.
"""

_SEVERITY_SCORE: dict[ErrorSeverity, float] = {
    ErrorSeverity.LOW: 1.0,
    ErrorSeverity.MEDIUM: 2.0,
    ErrorSeverity.HIGH: 3.0,
}
# Enforce exhaustive coverage at module load so adding a new severity
# without updating the score dict fails fast instead of raising KeyError
# deep inside an aggregation loop.
if set(_SEVERITY_SCORE.keys()) != set(ErrorSeverity):
    _SEVERITY_MISSING = set(ErrorSeverity) - set(_SEVERITY_SCORE.keys())
    _SEVERITY_MSG = (
        f"_SEVERITY_SCORE must cover every ErrorSeverity enum value; "
        f"missing: {sorted(v.name for v in _SEVERITY_MISSING)}"
    )
    raise RuntimeError(_SEVERITY_MSG)
"""Scoring for severity-weighted averages."""


class InMemoryErrorTaxonomyStore:
    """Process-local ring buffer of classification results.

    Args:
        max_results: Ring buffer capacity.  Oldest entries are evicted
            when the buffer is full.
    """

    def __init__(self, *, max_results: int = _DEFAULT_MAX_RESULTS) -> None:
        if max_results < 1:
            msg = f"max_results must be >= 1, got {max_results}"
            raise ValueError(msg)
        self._max_results = max_results
        self._results: deque[ClassificationResult] = deque(maxlen=max_results)
        self._lock = asyncio.Lock()

    async def on_classification(self, result: ClassificationResult) -> None:
        """Append a classification result.

        Implements :class:`ClassificationSink`.  Best-effort; swallows
        all exceptions except ``MemoryError`` / ``RecursionError``.
        """
        try:
            # Deep-copy the caller-owned result so a later mutation on
            # the outside reference cannot corrupt a stored entry.
            stored = copy.deepcopy(result)
            async with self._lock:
                evicted = len(self._results) == self._max_results
                self._results.append(stored)
            logger.debug(
                TAXONOMY_STORE_APPENDED,
                agent_id=result.agent_id,
                task_id=result.task_id,
                finding_count=result.finding_count,
            )
            if evicted:
                logger.info(
                    TAXONOMY_STORE_EVICTED,
                    max_results=self._max_results,
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TAXONOMY_STORE_APPEND_FAILED,
                agent_id=result.agent_id,
                task_id=result.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def query_findings(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[ErrorFinding, ...]:
        """Return findings classified within ``[since, until)``.

        Ordered newest-first.  Results outside the window are filtered
        out; findings from results inside the window are concatenated.
        """
        _validate_window(since, until)
        async with self._lock:
            snapshot = tuple(self._results)
        in_window = [
            r for r in reversed(snapshot) if _in_window(r.classified_at, since, until)
        ]
        # Deep-copy each finding before returning so callers cannot
        # mutate stored history via ``object.__setattr__`` side-channels
        # (the models are ``frozen=True`` for normal attribute access,
        # but the deep copy makes the read API defensively read-only).
        findings: list[ErrorFinding] = []
        for result in in_window:
            findings.extend(copy.deepcopy(f) for f in result.findings)
        return tuple(findings)

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgErrorSummary:
        """Roll findings up into an :class:`OrgErrorSummary`.

        Counts per category, averages severity per category (LOW=1,
        MEDIUM=2, HIGH=3), detects trend by comparing the older and
        newer half of the window against the per-finding classification
        timestamp, and picks the most-severe category by average
        severity.

        Returns:
            An :class:`OrgErrorSummary` carrying total findings,
            per-category summaries (with trend), and the most-severe
            category; an empty summary when the window is empty.
        """
        _validate_window(since, until)
        async with self._lock:
            snapshot = tuple(self._results)
        in_window = tuple(
            r for r in snapshot if _in_window(r.classified_at, since, until)
        )
        if not in_window:
            return OrgErrorSummary()
        total_findings = sum(r.finding_count for r in in_window)
        if total_findings == 0:
            return OrgErrorSummary()
        midpoint = since + (until - since) / 2
        categories = _build_category_summaries(in_window, midpoint=midpoint)
        most_severe = _pick_most_severe(categories)
        return OrgErrorSummary(
            total_findings=total_findings,
            categories=categories,
            most_severe_category=most_severe,
        )

    async def count(self) -> int:
        """Return current buffer size (not capacity)."""
        async with self._lock:
            return len(self._results)

    async def clear(self) -> None:
        """Drop all stored results."""
        async with self._lock:
            self._results.clear()


def _validate_window(since: datetime, until: datetime) -> None:
    """Reject inverted or naive windows before any scan happens.

    Raises:
        ValueError: When ``since`` / ``until`` are naive datetimes
            or when ``since >= until`` (inverted window).
    """
    if since.tzinfo is None or until.tzinfo is None:
        msg = "since/until must be timezone-aware"
        raise ValueError(msg)
    if since >= until:
        msg = (
            f"since ({since.isoformat()}) must be earlier than until "
            f"({until.isoformat()})"
        )
        raise ValueError(msg)


def _in_window(ts: datetime, since: datetime, until: datetime) -> bool:
    return since <= ts < until


def _build_category_summaries(
    in_window: Sequence[ClassificationResult],
    *,
    midpoint: datetime,
) -> tuple[ErrorCategorySummary, ...]:
    """Group findings by category with counts, severity avg, and trend.

    Each classification result carries its own ``classified_at``
    timestamp; findings are split between the older (classified before
    midpoint) and newer (at-or-after midpoint) halves of the window,
    so trend detection is timestamp-driven rather than position-based.

    Returns:
        Tuple of :class:`ErrorCategorySummary` rows in descending
        count order (ties broken by category name).
    """
    older_counts: dict[ErrorCategory, int] = {}
    newer_counts: dict[ErrorCategory, int] = {}
    severity_sums: dict[ErrorCategory, float] = {}
    total_counts: dict[ErrorCategory, int] = {}
    for result in in_window:
        bucket = newer_counts if result.classified_at >= midpoint else older_counts
        for finding in result.findings:
            bucket[finding.category] = bucket.get(finding.category, 0) + 1
            severity_sums[finding.category] = (
                severity_sums.get(finding.category, 0.0)
                + _SEVERITY_SCORE[finding.severity]
            )
            total_counts[finding.category] = total_counts.get(finding.category, 0) + 1
    summaries: list[ErrorCategorySummary] = []
    for category, count in total_counts.items():
        older = older_counts.get(category, 0)
        newer = newer_counts.get(category, 0)
        summaries.append(
            ErrorCategorySummary(
                category=NotBlankStr(category.value),
                count=count,
                avg_severity=severity_sums[category] / count,
                trend=_estimate_trend(newer=newer, older=older),
            ),
        )
    summaries.sort(key=lambda s: (-s.count, s.category))
    return tuple(summaries)


def _estimate_trend(*, newer: int, older: int) -> TrendDirection:
    """Compare newer-half vs older-half finding counts.

    Returns:
        DECLINING when ``newer > older`` (errors are rising, health is
        declining); IMPROVING when ``older > newer`` (errors are
        falling); STABLE only when the two halves have equal counts
        (including the both-zero case).

    A single-sided bucket -- e.g. ``newer=N`` with ``older=0`` -- is
    treated the same as any other comparison: a one-sided spike reads
    as DECLINING, a one-sided recovery reads as IMPROVING.  That
    matches what an operator actually wants to see when a fresh burst
    of errors shows up (or stops).
    """
    if newer > older:
        return TrendDirection.DECLINING
    if older > newer:
        return TrendDirection.IMPROVING
    return TrendDirection.STABLE


def _pick_most_severe(
    categories: tuple[ErrorCategorySummary, ...],
) -> NotBlankStr | None:
    """Return the category name with highest average severity.

    Ties broken by category count descending, then by name ascending
    for determinism.
    """
    if not categories:
        return None
    ranked = sorted(
        categories,
        key=lambda s: (-s.avg_severity, -s.count, s.category),
    )
    return ranked[0].category


__all__ = [
    "InMemoryErrorTaxonomyStore",
]
