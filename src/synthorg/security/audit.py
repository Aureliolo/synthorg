"""Append-only in-memory audit log for security evaluations."""

from collections import deque
from typing import Final

from pydantic import AwareDatetime  # noqa: TC002

from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUDIT_CLEARED,
    SECURITY_AUDIT_CONFIG_ERROR,
    SECURITY_AUDIT_EVICTION,
    SECURITY_AUDIT_RECORDED,
)
from synthorg.observability.metrics_hub import record_security_audit_fill_ratio
from synthorg.security.models import AuditEntry  # noqa: TC001

logger = get_logger(__name__)
_DEFAULT_MAX_ENTRIES: Final[int] = 100000
_DEFAULT_LIMIT: Final[int] = 100


class AuditLog:
    """Append-only in-memory security audit log.

    Thread-safety is not needed because the framework runs on a
    single event loop.  When ``max_entries`` is exceeded, the oldest
    entries are evicted with a warning.

    Future: backed by ``PersistenceBackend`` (see Memory design page).
    """

    def __init__(self, *, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        """Initialize the audit log.

        Args:
            max_entries: Maximum entries before oldest are evicted.

        Raises:
            ValueError: If *max_entries* < 1.
        """
        if max_entries < 1:
            msg = f"max_entries must be >= 1, got {max_entries}"
            logger.warning(
                SECURITY_AUDIT_CONFIG_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        self._max_entries = max_entries
        self._entries: deque[AuditEntry] = deque(maxlen=max_entries)
        self._total_recorded: int = 0

    def clear(self) -> None:
        """Reset all audit entries for test isolation."""
        previous_count = len(self._entries)
        previous_total = self._total_recorded
        self._entries.clear()
        self._total_recorded = 0
        # Reset the gauge so a previously near-full audit log doesn't
        # leave a stale ``1.0`` reading until the next record() call.
        record_security_audit_fill_ratio(ratio=0.0)
        logger.info(
            SECURITY_AUDIT_CLEARED,
            previous_count=previous_count,
            previous_total=previous_total,
        )

    def record(self, entry: AuditEntry) -> None:
        """Append an audit entry.

        Args:
            entry: The audit entry to record.
        """
        if len(self._entries) >= self._max_entries:
            logger.warning(
                SECURITY_AUDIT_EVICTION,
                max_entries=self._max_entries,
                total_recorded=self._total_recorded,
                note="oldest entry evicted to make room",
            )
        self._entries.append(entry)
        self._total_recorded += 1
        # Push fill ratio to Prometheus on every record. The append +
        # length read are O(1) so the cost is negligible; in exchange
        # operators get a queryable gauge that flips to ``1.0`` as the
        # eviction horizon approaches, enabling pre-eviction alerts
        # before evidence is lost.
        record_security_audit_fill_ratio(
            ratio=len(self._entries) / self._max_entries,
        )
        logger.debug(
            SECURITY_AUDIT_RECORDED,
            audit_id=entry.id,
            tool_name=entry.tool_name,
            verdict=entry.verdict,
        )

    @property
    def total_recorded(self) -> int:
        """Total entries ever recorded (including evicted)."""
        return self._total_recorded

    def query(  # noqa: PLR0913
        self,
        *,
        agent_id: str | None = None,
        tool_name: str | None = None,
        verdict: str | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[AuditEntry, ...]:
        """Query audit entries with optional filters.

        Filters are AND-combined.  Results are returned newest-first,
        up to *limit* entries.

        Args:
            agent_id: Filter by agent ID.
            tool_name: Filter by tool name.
            verdict: Filter by verdict string.
            risk_level: Filter by risk level.
            action_type: Filter by action type string.
            since: Entries before this datetime are excluded.
            until: Entries after this datetime are excluded.
            limit: Maximum results to return (must be >= 1).

        Returns:
            Tuple of matching entries, newest first.

        Raises:
            ValueError: If *limit* < 1 or *since* > *until*.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                SECURITY_AUDIT_CONFIG_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        if since is not None and until is not None and since > until:
            msg = "since must not be after until"
            logger.warning(
                SECURITY_AUDIT_CONFIG_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        results: list[AuditEntry] = []
        for entry in reversed(self._entries):
            if agent_id is not None and entry.agent_id != agent_id:
                continue
            if tool_name is not None and entry.tool_name != tool_name:
                continue
            if verdict is not None and entry.verdict != verdict:
                continue
            if risk_level is not None and entry.risk_level != risk_level:
                continue
            if action_type is not None and entry.action_type != action_type:
                continue
            if since is not None and entry.timestamp < since:
                continue
            if until is not None and entry.timestamp > until:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return tuple(results)

    def count(self) -> int:
        """Return the number of entries in the log."""
        return len(self._entries)

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        """Return all entries as a tuple (oldest first)."""
        return tuple(self._entries)
