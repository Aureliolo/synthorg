# module-kind: adapter
"""In-memory :class:`CompletionOracleReportRepository` implementation.

An async-safe in-memory dict keyed by ``execution_id``, bridging the reviewer
tool's ``put`` to the gate's single ``get`` within one evaluation. An in-memory
store suffices because each reviewer run is scoped to a single host process. The
repo is single-shot per ``execution_id``: a second ``put`` for the same key
raises :class:`CompletionOracleVerdictAlreadyExistsError`. ``get`` *consumes*
the entry (its one reader, the gate, has taken it, and the durable archive
already holds a queryable copy), so the store never grows unboundedly across a
long-lived process's completed tasks.
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

    A single ``asyncio.Lock`` serialises ``put`` and the consuming ``get`` so
    the single-shot-per-execution invariant holds under concurrent writers
    inside one event loop. Not safe for cross-thread access.
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
        """Return and CONSUME the report stored for ``execution_id``.

        The entry is removed once read: the gate is its only reader and the
        durable archive already holds a queryable copy, so retaining it would
        leak memory over a long-lived process's completed tasks.

        Returns:
            The stored :class:`CompletionOracleReport`.

        Raises:
            CompletionOracleVerdictNotFoundError: If no report is stored
                under ``execution_id``.
        """
        async with self._lock:
            report = self._reports.pop(execution_id, None)
        if report is None:
            raise CompletionOracleVerdictNotFoundError(execution_id=execution_id)
        return report
