# module-kind: declarative
"""Repository protocol for the durable red-team report archive.

A :class:`RedTeamReportRecord` row is the durable audit record of one
red-team gate evaluation. The in-process
:class:`~synthorg.security.redteam.protocol.RedTeamReportRepository`
(the put/get handshake the gate and the submit tool share) is unchanged;
this archive is the *cross-process* durability layer that survives the
run, so the flight-recorder read surface can answer "why was this
deliverable sent back?" long after the execution finished.

The repository composes :class:`AppendOnlyRepository`: a red-team verdict
is an immutable audit fact (``append`` only, never updated), filterable
by ``execution_id`` / ``task_id`` / ``verdict`` / ``red_team_agent_id``
and ordered newest-first by ``recorded_at``, with ``purge_before`` for
retention. A row is one attack EVENT rather than one execution: the gate
runs again whenever a task is decided, re-opened and decided again, so an
execution has as many reports as it had attacks, and the row carries its own
surrogate key.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, Self, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository
from synthorg.security.redteam.models import RedTeamReportRecord, RedTeamVerdict

__all__ = [
    "RedTeamReportArchiveRepository",
    "RedTeamReportFilterSpec",
]


class RedTeamReportFilterSpec(BaseModel):
    """Filter spec for ``RedTeamReportArchiveRepository.query``.

    All fields optional; an empty spec matches every record. The
    flight-recorder read surface queries by ``execution_id`` to surface
    the verdict for one run; an operator audit view may filter by
    ``task_id`` (every attempt for one deliverable), ``verdict`` (every
    blocked run), or ``red_team_agent_id`` (every verdict one adversary
    reached, which is what makes verdict quality comparable per agent).

    ``after_recorded_at`` + ``after_report_id`` are the keyset cursor, the
    two halves of one position: an archive is written to while it is read,
    and an offset would shift every later page by however many verdicts
    landed in between. Both are supplied or neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    execution_id: NotBlankStr | None = Field(default=None)
    task_id: NotBlankStr | None = Field(default=None)
    verdict: RedTeamVerdict | None = Field(default=None)
    red_team_agent_id: NotBlankStr | None = Field(default=None)
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
class RedTeamReportArchiveRepository(
    AppendOnlyRepository["RedTeamReportRecord", RedTeamReportFilterSpec],
    Protocol,
):
    """Append-only durable archive for red-team report records.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable record, ``query`` returns records newest-first under a
    filter, and ``purge_before`` enforces retention. The read surface
    fetches a run's latest verdict via
    ``query(RedTeamReportFilterSpec(execution_id=...), limit=1)``, and
    ``count`` is bespoke per D7 (see its docstring).

    Non-recoverable errors propagate as :class:`QueryError`.
    """

    @override
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride] -- domain-specific param name
        self, record: RedTeamReportRecord, /
    ) -> None:
        """Persist one attack event.

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
        filter_spec: RedTeamReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        """Return records matching the filter, newest-first by ``recorded_at``.

        Order is ``(recorded_at DESC, execution_id DESC, report_id DESC)``,
        the archive key closing a sort two reports of one execution can
        otherwise tie on.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    async def count(self, filter_spec: RedTeamReportFilterSpec, /) -> int:
        """Return how many records match the filter.

        Bespoke per D7: ``AppendOnlyRepository`` has no ``count``, and adding
        one there would oblige every append-only archive in the tree to grow
        an implementation for a question only this read surface asks.

        Paired with ``query`` for the same reason ``FilteredQueryRepository``
        pairs them: a caller that wants "how many deliverables did this
        adversary block" must not have to page the whole history to find out,
        and a count derived from one page silently reports a window as a total.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def count_by_verdict(
        self, filter_spec: RedTeamReportFilterSpec, /
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

        ``threshold`` must be timezone-aware; passing a naive value is a
        contract violation rejected at the persistence boundary by an explicit
        ``if threshold.tzinfo is None`` guard in each concrete implementation
        (``normalize_utc`` coerces naive datetimes to UTC rather than raising,
        so it cannot enforce this on its own), keeping the cut-off from drifting
        with the session timezone. The annotation is a plain ``datetime``
        (matching the other repository protocols) rather than ``AwareDatetime``
        so runtime type-checking does not reject an aware ``datetime`` instance.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query fails.
        """
        ...
