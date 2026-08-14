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

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

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
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    execution_id: NotBlankStr | None = Field(default=None)
    task_id: NotBlankStr | None = Field(default=None)
    verdict: RedTeamVerdict | None = Field(default=None)
    red_team_agent_id: NotBlankStr | None = Field(default=None)


@runtime_checkable
class RedTeamReportArchiveRepository(
    AppendOnlyRepository["RedTeamReportRecord", RedTeamReportFilterSpec],
    Protocol,
):
    """Append-only durable archive for red-team report records.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable record, ``query`` returns records newest-first under a
    filter, and ``purge_before`` enforces retention. No bespoke methods
    beyond the generic surface; the read surface fetches a run's latest
    verdict via ``query(RedTeamReportFilterSpec(execution_id=...),
    limit=1)``.

    Non-recoverable errors propagate as :class:`QueryError`.
    """

    @override
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride] -- domain-specific param name
        self, record: RedTeamReportRecord, /
    ) -> None:
        """Persist one attack event.

        Raises:
            DuplicateRecordError: On a uniqueness violation, which a
                re-attacked execution no longer is: it writes an ordinary
                second row.
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

        Paired with ``query`` for the same reason ``FilteredQueryRepository``
        pairs them: a caller that wants "how many deliverables did this
        adversary block" must not have to page the whole history to find out,
        and a count derived from one page silently reports a window as a total.

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
