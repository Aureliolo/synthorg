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
``purge_before`` for retention. A row is one review EVENT, not one execution:
an execution decided, re-opened and decided again holds two rows, ordered by
``recorded_at``, and the surrogate ``report_id`` is what keeps them apart.
Nothing dedupes on ``execution_id``, so an operator reading a per-agent count
is counting reviews.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, Self, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
    audit view may filter by ``task_id`` (every attempt for one deliverable),
    ``verdict`` (every rejected / escalated run), or ``reviewer_agent_id``
    (every verdict one reviewer reached, which is what makes verdict quality
    comparable per agent).

    ``after_recorded_at`` + ``after_report_id`` are the keyset cursor, the
    two halves of one position: an archive is written to while it is read,
    and an offset would shift every later page by however many verdicts
    landed in between. Both are supplied or neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    execution_id: NotBlankStr | None = Field(default=None)
    task_id: NotBlankStr | None = Field(default=None)
    verdict: CompletionOracleVerdict | None = Field(default=None)
    reviewer_agent_id: NotBlankStr | None = Field(default=None)
    after_recorded_at: AwareDatetime | None = Field(default=None)
    after_report_id: int | None = Field(default=None)

    @model_validator(mode="after")
    def _cursor_halves_travel_together(self) -> Self:
        """Reject half a keyset position.

        Returns:
            The validated spec.

        Raises:
            ValueError: If exactly one cursor half is supplied. One half
                alone reads as a plain timestamp filter, which silently
                drops every row sharing the boundary instant.
        """
        if (self.after_recorded_at is None) != (self.after_report_id is None):
            msg = (
                "after_recorded_at and after_report_id are one cursor; "
                "supply both or neither"
            )
            raise ValueError(msg)
        return self


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
    ``purge_before`` enforces retention. Database errors raise
    :class:`QueryError`.
    """

    @override
    # ``record`` keeps the archive's domain vocabulary rather than the base
    # protocol's generic parameter name; the override is otherwise compatible.
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, record: CompletionOracleReportRecord, /
    ) -> None:
        """Persist one review event (append-only; a re-review is a second row).

        Raises:
            DuplicateRecordError: On a uniqueness violation. No column pair
                is unique today, so nothing reachable raises it; the branch
                is kept because a future index would surface here.
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

        Order is ``(recorded_at DESC, execution_id DESC, report_id DESC)``.
        The surrogate key is the final tiebreaker so two reviews of one
        execution recorded at the same instant still page deterministically.

        Raises:
            QueryError: If the database query fails or pagination args are invalid.
        """
        ...

    async def count(self, filter_spec: CompletionOracleReportFilterSpec, /) -> int:
        """Return how many records match the filter.

        Bespoke per D7: ``AppendOnlyRepository`` has no ``count``, and adding
        one there would oblige every append-only archive in the tree to grow
        an implementation for a question only this read surface asks.

        Paired with ``query`` for the same reason ``FilteredQueryRepository``
        pairs them: a caller that wants "how many verdicts did this reviewer
        reject" must not have to page the whole history to find out, and a
        count derived from one page silently reports a window as a total.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def count_by_verdict(
        self, filter_spec: CompletionOracleReportFilterSpec, /
    ) -> Mapping[str, int]:
        """Return the matching row count for every verdict kind present.

        Bespoke per D7, for the same reason ``count`` is. One grouped read
        rather than one ``count`` per kind: the per-kind calls are separate
        statements, so their sum is a total across as many instants as there
        are verdict kinds, and a verdict landing between two of them is
        counted in one and missing from the other.

        A kind with no rows is absent from the mapping rather than present
        with a zero: the store reports what it holds, and filling the gaps
        is the caller's vocabulary, not the archive's.

        Raises:
            QueryError: If the database query fails.
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
