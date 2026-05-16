"""Audit repository protocol."""

from datetime import datetime  # noqa: TC003 -- referenced by Protocol signatures
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository
from synthorg.security.models import AuditVerdictStr  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.security.models import AuditEntry

__all__ = [
    "AuditFilterSpec",
    "AuditRepository",
]


class AuditFilterSpec(BaseModel):
    """Filter spec for ``AuditRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by agent identifier",
    )
    action_type: NotBlankStr | None = Field(
        default=None,
        description="Filter by action type string",
    )
    verdict: AuditVerdictStr | None = Field(
        default=None,
        description="Filter by verdict (allow/deny/escalate/output_scan)",
    )
    risk_level: ApprovalRiskLevel | None = Field(
        default=None,
        description="Filter by risk level",
    )
    since: datetime | None = Field(
        default=None,
        description="Only return entries at or after this timestamp",
    )
    until: datetime | None = Field(
        default=None,
        description="Only return entries at or before this timestamp",
    )


@runtime_checkable
class AuditRepository(
    AppendOnlyRepository["AuditEntry", AuditFilterSpec],
    Protocol,
):
    """Append-only persistence + query interface for AuditEntry.

    Composes :class:`AppendOnlyRepository` (ADR-0001). Audit entries
    are immutable records of security evaluations. No update operations
    are provided to preserve audit integrity.

    The single delete-style operation is :meth:`purge_before`, the
    retention sweeper used to enforce the operator-configurable
    ``security.audit_retention_days`` window. This is a deliberate
    exception to the append-only rule; see :meth:`purge_before`
    for the retention-vs-forensic tradeoff.
    """

    async def append(self, entry: AuditEntry) -> None:
        """Persist an audit entry (append-only).

        Args:
            entry: The audit entry to persist.

        Raises:
            DuplicateRecordError: If an entry with the same ID exists.
            QueryError: If the operation fails.
        """
        ...

    async def query(
        self,
        filter_spec: AuditFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AuditEntry, ...]:
        """Return audit entries matching the filter spec (paginated).

        Results are ordered by timestamp descending (newest first).

        Args:
            filter_spec: Audit filter specification with optional filters.
            limit: Maximum number of entries to return (must be >= 1).
            offset: Number of entries to skip (for pagination).

        Returns:
            Matching audit entries as a tuple.

        Raises:
            QueryError: If the operation fails, *limit* < 1, or
                *until* is earlier than *since* in the filter spec.
        """
        ...

    async def purge_before(self, cutoff: datetime) -> int:
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
