"""ReportsService -- thin facade over analytics + an in-memory store.

Supports three MCP tools:

* ``synthorg_reports_list`` -- paginated listing.
* ``synthorg_reports_get`` -- fetch by ID.
* ``synthorg_reports_generate`` -- build a fresh report from a named
  template against the current signals snapshot.

The store is process-local (``{id: Report}``) and safe for dev/test;
a durable backend plugs in behind :class:`ReportsRepository` without
touching callers.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.reports.models import Report, ReportStatus
from synthorg.observability import get_logger
from synthorg.observability.events.reporting import (
    REPORT_GENERATED,
    REPORT_LISTED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from synthorg.meta.analytics.service import AnalyticsService

logger = get_logger(__name__)
_DEFAULT_WINDOW_DAYS: Final[int] = 7
_DEFAULT_LIMIT: Final[int] = 50

_SUPPORTED_TEMPLATES: frozenset[str] = frozenset(
    {
        "org_overview",
        "metrics_snapshot",
        "trend_summary",
    },
)
"""Known report templates.

Unknown template names are rejected at ``generate()`` so MCP callers
get a typed error rather than an opaque empty payload.  Adding a
template requires only updating this set and :meth:`_render`.
"""


class ReportsService:
    """In-process report store + generator.

    Args:
        analytics: Analytics facade used to source data for
            report templates.
        window_days: Default observation window (days) when a
            generator call does not supply ``since`` / ``until``.
    """

    def __init__(
        self,
        *,
        analytics: AnalyticsService,
        window_days: int = _DEFAULT_WINDOW_DAYS,
    ) -> None:
        if window_days < 1:
            msg = f"window_days must be >= 1, got {window_days}"
            raise ValueError(msg)
        self._analytics = analytics
        self._window_days = window_days
        self._reports: dict[UUID, Report] = {}
        self._lock = asyncio.Lock()

    # ── Read ──────────────────────────────────────────────────────────

    async def list_reports(
        self,
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[tuple[Report, ...], int]:
        """Return a page of reports ordered newest-first.

        Returns:
            Tuple of ``(page, total_count)``.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` < 1.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        async with self._lock:
            # Sort by (generated_at, insertion_idx) so equal timestamps
            # break toward the *later*-inserted report when reversed,
            # guaranteeing a stable newest-first ordering when two
            # reports happen to share a timestamp (common in test
            # harnesses that pin ``datetime.now``).
            enumerated = tuple(enumerate(self._reports.values()))
            ordered = tuple(
                r
                for _idx, r in sorted(
                    enumerated,
                    key=lambda pair: (pair[1].generated_at, pair[0]),
                    reverse=True,
                )
            )
        total = len(ordered)
        page = ordered[offset : offset + limit]
        logger.info(
            REPORT_LISTED,
            offset=offset,
            limit=limit,
            total=total,
        )
        return page, total

    async def get_report(self, report_id: UUID) -> Report | None:
        """Return a report by ID or ``None`` when absent.

        Returns:
            The ``Report`` value when present, ``None`` otherwise.
        """
        async with self._lock:
            return self._reports.get(report_id)

    # ── Write (generator) ────────────────────────────────────────────

    async def generate_report(
        self,
        *,
        template: NotBlankStr,
        author_id: NotBlankStr,
        options: Mapping[str, str] | None = None,
    ) -> Report:
        """Render a fresh report from ``template`` and store it.

        Raises:
            ValueError: When ``template`` is not in the known set.

        Returns:
            ``Report`` instance.
        """
        if template not in _SUPPORTED_TEMPLATES:
            msg = (
                f"Unknown report template {template!r}; supported: "
                f"{sorted(_SUPPORTED_TEMPLATES)}"
            )
            raise ValueError(msg)
        now = datetime.now(UTC)
        since = now - timedelta(days=self._window_days)
        title, content = await self._render(template=template, since=since, until=now)
        report = Report(
            template=template,
            title=NotBlankStr(title),
            status=ReportStatus.READY,
            author_id=author_id,
            content=content,
            options=dict(options or {}),
        )
        async with self._lock:
            self._reports[report.id] = report
        logger.info(
            REPORT_GENERATED,
            report_id=str(report.id),
            template=template,
            author_id=author_id,
        )
        return report

    # ── Rendering helpers ────────────────────────────────────────────

    async def _render(
        self,
        *,
        template: str,
        since: datetime,
        until: datetime,
    ) -> tuple[str, dict[str, object]]:
        """Dispatch to the template-specific render helper.

        Every template in :data:`_SUPPORTED_TEMPLATES` must be matched
        explicitly below.  Unknown templates raise instead of silently
        falling through to a default renderer, which would mask bugs
        when a new template is added to the allowlist without a
        matching branch here.

        Returns:
            Tuple of the declared element types.

        Raises:
            RuntimeError: Raised on the corresponding failure path.
        """
        if template == "org_overview":
            overview = await self._analytics.get_overview(since=since, until=until)
            return ("Org overview", overview.model_dump(mode="json"))
        if template == "metrics_snapshot":
            snapshot = await self._analytics.get_current_metrics(
                since=since,
                until=until,
            )
            return ("Metrics snapshot", snapshot.model_dump(mode="json"))
        if template == "trend_summary":
            trends = await self._analytics.get_trends(since=since, until=until)
            return ("Trend summary", trends.model_dump(mode="json"))
        msg = f"_render has no branch for allowed template {template!r}"
        raise RuntimeError(msg)

    # ── Test helper ──────────────────────────────────────────────────

    async def _clear_for_tests(self) -> None:
        """Drop the in-memory store -- test-only convenience."""
        async with self._lock:
            self._reports.clear()


__all__ = [
    "ReportsService",
]
