"""Audit repository protocol."""

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.security.models import AuditEntry, AuditVerdictStr  # noqa: TC001


@runtime_checkable
class AuditRepository(Protocol):
    """Append-only persistence + query interface for AuditEntry.

    Audit entries are immutable records of security evaluations.
    No update operations are provided to preserve audit integrity.

    The single delete-style operation is :meth:`purge_before`, the
    retention sweeper used to enforce the operator-configurable
    ``security.audit_retention_days`` window.  This is a deliberate
    exception, not a per-row mutation API; see :meth:`purge_before`
    for the retention/forensic tradeoff.
    """

    async def save(self, entry: AuditEntry) -> None:
        """Persist an audit entry (append-only).

        Args:
            entry: The audit entry to persist.

        Raises:
            DuplicateRecordError: If an entry with the same ID exists.
            QueryError: If the operation fails.
        """
        ...

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: NotBlankStr | None = None,
        action_type: NotBlankStr | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:
        """Query audit entries with optional filters.

        Filters are AND-combined. Results are ordered by timestamp
        descending (newest first).

        Args:
            agent_id: Filter by agent identifier.
            action_type: Filter by action type string.
            verdict: Filter by verdict string.
            risk_level: Filter by risk level.
            since: Only return entries at or after this timestamp.
            until: Only return entries at or before this timestamp.
            limit: Maximum number of entries to return (must be >= 1).

        Returns:
            Matching audit entries as a tuple.

        Raises:
            QueryError: If the operation fails, *limit* < 1, or
                *until* is earlier than *since*.
        """
        ...

    async def purge_before(self, cutoff: AwareDatetime) -> int:
        """Delete audit entries older than *cutoff* (CFG-1 audit).

        This is the one exception to the append-only rule: it powers
        the retention sweeper which enforces the operator-configurable
        ``security.audit_retention_days`` window. Rows are removed
        permanently; the retention-vs-forensic tradeoff is decided at
        the retention-window level, not per row.

        Args:
            cutoff: Entries strictly older than this UTC timestamp
                are deleted.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the operation fails.
        """
        ...
