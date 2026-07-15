# module-kind: declarative
"""Repository protocol for the durable completion-oracle verdict archive.

A :class:`CompletionOracleReportRecord` row is the durable audit record of
one peer-review gate evaluation. The in-process
:class:`~synthorg.engine.completion_oracle.protocol.CompletionOracleReportRepository`
(the put/get handshake the gate and the submit tool share) is unchanged;
this archive is the *cross-process* durability layer that survives the run,
so the flight-recorder read surface can answer "why was this deliverable sent
back?" long after the execution finished.

The repository composes :class:`AppendOnlyRepository`: a verdict is an
immutable audit fact (``append`` only), filterable by ``execution_id`` /
``task_id`` / ``verdict`` and ordered newest-first by ``recorded_at``, with
``purge_before`` for retention. Single-shot per ``execution_id`` is enforced
by a primary key; a second append raises
:class:`~synthorg.core.persistence_errors.DuplicateRecordError`.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository

__all__ = [
    "CompletionOracleReportArchiveRepository",
    "CompletionOracleReportFilterSpec",
]


class CompletionOracleReportFilterSpec(BaseModel):
    """Filter spec for ``CompletionOracleReportArchiveRepository.query``.

    All fields optional; an empty spec matches every record. The read surface
    queries by ``execution_id`` to surface one run's verdict; an operator
    audit view may filter by ``task_id`` (every attempt for one deliverable)
    or ``verdict`` (every rejected / escalated run).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    execution_id: NotBlankStr | None = Field(default=None)
    task_id: NotBlankStr | None = Field(default=None)
    verdict: CompletionOracleVerdict | None = Field(default=None)


@runtime_checkable
class CompletionOracleReportArchiveRepository(
    AppendOnlyRepository[
        CompletionOracleReportRecord, CompletionOracleReportFilterSpec
    ],
    Protocol,
):
    """Append-only durable archive for completion-oracle verdict records.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one immutable
    record, ``query`` returns records newest-first under a filter, and
    ``purge_before`` enforces retention. A duplicate ``execution_id`` raises
    :class:`DuplicateRecordError`; other database errors raise :class:`QueryError`.
    """

    @override
    # ``record`` keeps the archive's domain vocabulary rather than the base
    # protocol's generic parameter name; the override is otherwise compatible.
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, record: CompletionOracleReportRecord, /
    ) -> None:
        """Persist one record (append-only; a duplicate execution is a violation).

        Raises:
            DuplicateRecordError: If a record already exists for the same
                ``execution_id``.
            QueryError: On other database errors.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: CompletionOracleReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CompletionOracleReportRecord, ...]:
        """Return records matching the filter, newest-first by ``recorded_at``.

        Order is ``(recorded_at DESC, execution_id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args are invalid.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete records with ``recorded_at < threshold``. Returns rows removed.

        ``threshold`` must be timezone-aware; a naive value is rejected at the
        persistence boundary by an explicit guard in each concrete implementation.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query fails.
        """
        ...
