"""MeetingService -- read facade over :class:`MeetingOrchestrator`.

Reads are direct: ``list_meetings`` returns the orchestrator's audit
records newest-first, ``get_meeting`` looks up by ID. ``delete_meeting``
removes a single record from the orchestrator's in-memory store and
emits an audit-grade :data:`COMMUNICATION_MEETING_DELETED` event.
Create / update remain capability-gated because meetings are
produced by executing a :class:`MeetingProtocol` via the engine, not
by ad-hoc record insertion.
"""

from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMMUNICATION_MEETING_DELETED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.communication.meeting.enums import MeetingStatus
    from synthorg.communication.meeting.models import MeetingRecord
    from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_CREATE_CAP = "meeting_create"
_CREATE_DETAIL = (
    "meetings are produced by executing a MeetingProtocol via the "
    "engine loop, not by ad-hoc record insertion; submit a meeting "
    "type + participants through the scheduler instead"
)
_UPDATE_CAP = "meeting_update"
_UPDATE_DETAIL = (
    "MeetingRecord is an immutable audit entry; status transitions "
    "happen inside the orchestrator, not via MCP edits"
)


class MeetingService:
    """Facade over :class:`MeetingOrchestrator` for MCP reads.

    Args:
        orchestrator: The meeting orchestrator holding audit records.
    """

    def __init__(self, *, orchestrator: MeetingOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def list_meetings(
        self,
        *,
        status: MeetingStatus | None = None,
        meeting_type: NotBlankStr | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[Sequence[MeetingRecord], int]:
        """Return records newest-first, optionally filtered and paginated.

        Returns ``(items, total)`` where ``total`` is the unfiltered
        count for the applied filter (status / meeting_type) so the
        handler can build the pagination envelope without slicing a
        second time.

        Raises:
            ValueError: If ``offset`` is negative, or if ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        records = self._orchestrator.get_records()
        if status is not None:
            records = tuple(r for r in records if r.status == status)
        if meeting_type is not None:
            records = tuple(r for r in records if r.meeting_type_name == meeting_type)
        # ``MeetingRecord`` has no timestamp; the orchestrator preserves
        # chronological append order, so we reverse to surface newest-first.
        ordered = tuple(reversed(records))
        total = len(ordered)
        end = total if limit is None else offset + limit
        return (ordered[offset:end], total)

    async def get_meeting(
        self,
        meeting_id: NotBlankStr,
    ) -> MeetingRecord | None:
        """Return a meeting record by ID or ``None`` when absent."""
        for record in self._orchestrator.get_records():
            if record.meeting_id == meeting_id:
                return record
        return None

    async def create_meeting(self) -> None:
        """Reject creation with a typed ``not_supported`` error."""
        raise CapabilityNotSupportedError(_CREATE_CAP, _CREATE_DETAIL)

    async def update_meeting(self) -> None:
        """Reject update with a typed ``not_supported`` error."""
        raise CapabilityNotSupportedError(_UPDATE_CAP, _UPDATE_DETAIL)

    async def delete_meeting(
        self,
        *,
        meeting_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Delete a meeting record by id.

        Removes the record from the orchestrator's in-memory audit
        store. Returns ``True`` when a row was removed, ``False`` when
        the id did not exist.

        Args:
            meeting_id: Meeting record identifier.
            actor_id: Authenticated actor that initiated the delete
                (drives the audit log).
            reason: Operator-supplied justification (drives the audit
                log).
        """
        deleted = self._orchestrator.delete_record(meeting_id)
        if deleted:
            logger.info(
                COMMUNICATION_MEETING_DELETED,
                meeting_id=meeting_id,
                actor_id=actor_id,
                reason=reason,
            )
        return deleted


__all__ = [
    "MeetingService",
]
