# module-kind: adapter
"""In-memory :class:`CompletionOracleReportRepository` implementation.

An async-safe in-memory dict keyed by ``execution_id``. An in-memory store
suffices because each reviewer run is scoped to a single host process. The
repo is single-shot per ``execution_id``: a second ``put`` for the same key
raises :class:`CompletionOracleVerdictAlreadyExistsError`.
"""

import asyncio

from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.errors import (
    CompletionOracleVerdictAlreadyExistsError,
    CompletionOracleVerdictNotFoundError,
)
from synthorg.engine.completion_oracle.review_models import CompletionOracleReport


class InMemoryCompletionOracleReportRepository:
    """Async-safe in-memory store of :class:`CompletionOracleReport` entries.

    A single ``asyncio.Lock`` serialises ``put`` so the single-shot-per-execution
    invariant holds under concurrent writers inside one event loop. ``get`` is
    lock-free: read-after-write within the same event loop is ordered by the
    GIL on the underlying dict. Not safe for cross-thread access.
    """

    def __init__(self) -> None:
        self._reports: dict[str, CompletionOracleReport] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        *,
        execution_id: NotBlankStr,
        report: CompletionOracleReport,
    ) -> None:
        """Persist ``report`` under ``execution_id`` (single-shot).

        Raises:
            CompletionOracleVerdictAlreadyExistsError: If a report is already
                stored under ``execution_id``.
        """
        async with self._lock:
            if execution_id in self._reports:
                raise CompletionOracleVerdictAlreadyExistsError(
                    execution_id=execution_id
                )
            self._reports[execution_id] = report

    async def get(
        self,
        *,
        execution_id: NotBlankStr,
    ) -> CompletionOracleReport:
        """Return the report stored for ``execution_id``.

        Returns:
            The stored :class:`CompletionOracleReport`.

        Raises:
            CompletionOracleVerdictNotFoundError: If no report is stored
                under ``execution_id``.
        """
        report = self._reports.get(execution_id)
        if report is None:
            raise CompletionOracleVerdictNotFoundError(execution_id=execution_id)
        return report
