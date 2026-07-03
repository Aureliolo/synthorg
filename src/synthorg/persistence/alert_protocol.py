# module-kind: declarative
"""AlertRepository protocol.

Append-only durable log of every proactive :class:`Alert` the
org-inflection monitor emits. ``LoggingAlertSink`` stays the always-on
log path; this repository backs the ``/meta/alerts`` read endpoint and
the ``alert_id`` resolution the ``/meta/chat`` handler needs to answer
a question scoped to a specific alert.
"""

from datetime import datetime
from typing import Literal, Protocol, Self, override, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from synthorg.meta.chief_of_staff.models import Alert
from synthorg.meta.models import RuleSeverity
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class AlertFilterSpec(BaseModel):
    """Filter spec for ``AlertRepository.query`` (ADR-0001).

    All fields optional; an empty spec matches every alert. No
    ``affected_domain`` filter: ``affected_domains`` is stored as a JSON
    array column and every filter spec in this codebase is
    columnar-scalar, so filtering on it would require either a
    full-table scan (SQLite has no JSONB-containment support) or a
    child table -- a deliberate limitation, not an oversight.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    severity: RuleSeverity | None = None
    alert_type: Literal["inflection", "threshold", "trend"] | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_window_order(self) -> Self:
        """Reject an inverted ``[since, until)`` window.

        An inverted window would silently return zero rows from the
        backend rather than surfacing the misconfiguration.

        Returns:
            The validated instance.

        Raises:
            ValueError: When both bounds are set and ``since >= until``.
        """
        since = self.since
        until = self.until
        if since is not None and until is not None and since >= until:
            msg = (
                f"since ({since.isoformat()}) must be earlier than "
                f"until ({until.isoformat()})"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class AlertRepository(AppendOnlyRepository[Alert, AlertFilterSpec], Protocol):
    """Append-only persistence for proactive org alerts.

    Composes :class:`AppendOnlyRepository` (ADR-0001). Bespoke per D7:

    * ``get_by_id`` resolves a single alert by its domain UUID -- needed
      to turn the ``/meta/chat`` request's ``alert_id`` into a full
      ``Alert`` for ``ChiefOfStaffChat.explain_alert``; the generic
      ``AppendOnlyRepository`` surface has no single-row fetch.
    """

    @override
    async def append(self, event: Alert, /) -> None:
        """Persist an alert (append-only).

        Args:
            event: The alert to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: AlertFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Alert, ...]:
        """Query alerts with optional filters and pagination.

        Args:
            filter_spec: Optional severity / alert_type / window filters.
            limit: Maximum rows to return.
            offset: Rows to skip before applying limit.

        Returns:
            Matching alerts as a tuple, ordered newest-first by
            ``emitted_at``.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete alerts emitted before threshold (retention).

        Args:
            threshold: Alerts older than this are deleted.

        Returns:
            Number of rows removed.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_by_id(self, alert_id: UUID, /) -> Alert | None:
        """Resolve one alert by its domain UUID.

        Args:
            alert_id: The alert's domain identifier.

        Returns:
            The matching alert, or ``None`` when no such alert exists.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...


__all__ = ["AlertFilterSpec", "AlertRepository"]
