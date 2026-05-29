"""In-memory :class:`RedTeamReportRepository` implementation.

The v1 repository is an async-safe in-memory dict keyed by
``execution_id``. An in-memory store suffices because each agent run is
scoped to a single host process. The repo is single-shot per
``execution_id``: a second ``put`` for the same key raises
:class:`RedTeamReportAlreadyExistsError`.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.security.redteam.errors import (
    RedTeamReportAlreadyExistsError,
    RedTeamReportNotFoundError,
)

if TYPE_CHECKING:
    from synthorg.security.redteam.models import RedTeamReport


class InMemoryRedTeamReportRepository:
    """Async-safe in-memory store of :class:`RedTeamReport` entries.

    Concurrency model:

    A single ``asyncio.Lock`` serialises ``put`` so the
    single-shot-per-execution invariant holds under concurrent writers
    inside one event loop. ``get`` is lock-free: read-after-write
    within the same event loop is ordered by Python's GIL on the
    underlying dict. The repo is NOT safe for cross-thread access;
    callers running multiple event loops or threads must own
    serialisation themselves.
    """

    def __init__(self) -> None:
        self._reports: dict[str, RedTeamReport] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        *,
        execution_id: NotBlankStr,
        report: RedTeamReport,
    ) -> None:
        """Persist ``report`` under ``execution_id`` (single-shot).

        Raises:
            RedTeamReportAlreadyExistsError: If a report is already
                stored under ``execution_id``.
        """
        async with self._lock:
            if execution_id in self._reports:
                raise RedTeamReportAlreadyExistsError(execution_id=execution_id)
            self._reports[execution_id] = report

    async def get(
        self,
        *,
        execution_id: NotBlankStr,
    ) -> RedTeamReport:
        """Return the report stored for ``execution_id``.

        Returns:
            The stored ``RedTeamReport``.

        Raises:
            RedTeamReportNotFoundError: If no report is stored under
                ``execution_id``.
        """
        report = self._reports.get(execution_id)
        if report is None:
            raise RedTeamReportNotFoundError(execution_id=execution_id)
        return report
