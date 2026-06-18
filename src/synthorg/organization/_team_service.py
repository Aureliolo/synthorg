# module-kind: service
"""In-memory team CRUD for the organization MCP surface.

``TeamService`` holds the operator-authored team records the MCP handlers
mutate. Kept beside the company/department/role services in
:mod:`synthorg.organization.services` (which still owns the ``UNSET``
sentinel this module reuses) so that module stays within its size budget.
"""

import asyncio
import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.company import (
    TEAM_CREATED_VIA_MCP,
    TEAM_DELETED_VIA_MCP,
    TEAM_UPDATED_VIA_MCP,
)
from synthorg.organization.services import UNSET, UnsetType

logger = get_logger(__name__)


class _TeamRecord:
    __slots__ = ("created_at", "department_id", "id", "name", "updated_at")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        department_id: str | None,
        created_at: datetime,
        updated_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.department_id = department_id
        self.created_at = created_at
        self.updated_at = updated_at if updated_at is not None else created_at

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "department_id": self.department_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class TeamService:
    """Team CRUD.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict.
    """

    def __init__(self) -> None:
        self._teams: dict[UUID, _TeamRecord] = {}
        self._lock = asyncio.Lock()

    async def list_teams(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[_TeamRecord, ...], int]:
        """Return paginated teams newest-first plus unfiltered total.

        Args:
            offset: Non-negative page offset.
            limit: Optional positive page size; ``None`` returns every
                team from ``offset`` onwards.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        async with self._lock:
            snapshot = tuple(copy.deepcopy(t) for t in self._teams.values())
        ordered = tuple(
            sorted(snapshot, key=lambda t: t.created_at, reverse=True),
        )
        total = len(ordered)
        end = total if limit is None else offset + limit
        return ordered[offset:end], total

    async def get_team(self, team_id: NotBlankStr) -> _TeamRecord | None:
        """Fetch a single team by UUID or ``None`` if not found.

        Returns:
            A deep copy of the stored team, or ``None`` when the id is
            malformed or no such team exists.
        """
        try:
            key = UUID(team_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._teams.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_team(
        self,
        *,
        name: NotBlankStr,
        actor_id: NotBlankStr,
        department_id: NotBlankStr | None = None,
    ) -> _TeamRecord:
        """Create a team, auditing the event on success.

        Returns:
            A deep copy of the newly created team record.
        """
        record = _TeamRecord(
            id=uuid4(),
            name=name,
            department_id=department_id,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._teams[record.id] = record
        logger.info(
            TEAM_CREATED_VIA_MCP,
            team_id=str(record.id),
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def update_team(
        self,
        *,
        team_id: NotBlankStr,
        actor_id: NotBlankStr,
        name: NotBlankStr | None = None,
        department_id: NotBlankStr | None | UnsetType = UNSET,
    ) -> _TeamRecord | None:
        """Update a team; ``department_id=None`` clears the field.

        The default ``department_id=UNSET`` sentinel means "leave
        unchanged"; pass ``department_id=None`` explicitly to clear a
        team's department assignment.

        Returns:
            A deep copy of the updated team, or ``None`` when the id is
            malformed or no such team exists.
        """
        try:
            key = UUID(team_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._teams.get(key)
            if record is None:
                return None
            if name is not None:
                record.name = name
            if not isinstance(department_id, UnsetType):
                record.department_id = department_id
            record.updated_at = datetime.now(UTC)
            returned = copy.deepcopy(record)
        logger.info(
            TEAM_UPDATED_VIA_MCP,
            team_id=team_id,
            actor_id=actor_id,
        )
        return returned

    async def delete_team(
        self,
        *,
        team_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove a team; emit the audit event only on real removal.

        Returns:
            ``True`` when a team was removed, ``False`` when the id is
            malformed or no such team exists.
        """
        try:
            key = UUID(team_id)
        except ValueError:
            return False
        async with self._lock:
            removed = self._teams.pop(key, None) is not None
        if removed:
            logger.info(
                TEAM_DELETED_VIA_MCP,
                team_id=team_id,
                actor_id=actor_id,
                reason=reason,
                removed=removed,
            )
        return removed
