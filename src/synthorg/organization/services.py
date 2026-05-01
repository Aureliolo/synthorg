# ruff: noqa: EM101
"""Organization facades for the MCP handler layer.

Company read/versions, department CRUD + health, team CRUD, and
role-version history.  Writes route through ``org_mutation_service``
where it already owns the flow; other paths use in-memory stores until
durable repositories land.

The file-level ``EM101`` suppression is intentional: every capability
gap in this module is raised via the :func:`_capability` factory which
builds a :class:`CapabilityNotSupportedError` from a short identifier
and operator-readable reason -- both string literals by design so
capability telemetry (logged via :data:`MCP_HANDLER_CAPABILITY_GAP`)
has a stable, grep-able message.  Hoisting them to ``msg = ...``
locals would obscure the one-line intent with no runtime benefit.
"""

import asyncio
import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.observability import get_logger
from synthorg.observability.events.company import (
    COMPANY_UPDATED_VIA_MCP,
    DEPARTMENT_CREATED_VIA_MCP,
    DEPARTMENT_DELETED_VIA_MCP,
    DEPARTMENT_UPDATED_VIA_MCP,
    DEPARTMENTS_REORDERED_VIA_MCP,
    TEAM_CREATED_VIA_MCP,
    TEAM_DELETED_VIA_MCP,
    TEAM_UPDATED_VIA_MCP,
)
from synthorg.organization.models import UpdateCompanyRequest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from synthorg.api.services.org_mutations import OrgMutationService
    from synthorg.core.types import NotBlankStr


logger = get_logger(__name__)


class UnsetType:
    """Sentinel type for "field not provided" distinct from ``None``.

    Used by update operations where ``None`` is a legitimate value the
    caller may want to persist (e.g. clearing a ``department_id``) and
    must be distinguished from "leave this field unchanged".
    """

    _instance: UnsetType | None = None

    def __new__(cls) -> UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = UnsetType()


def _capability(capability: str, detail: str) -> CapabilityNotSupportedError:
    return CapabilityNotSupportedError(capability, detail)


# ── CompanyReadService ──────────────────────────────────────────────


class CompanyReadService:
    """Read + light mutation facade over the company/org surface."""

    def __init__(self, *, org_mutation: OrgMutationService) -> None:
        self._org = cast("Any", org_mutation)

    async def get_company(self) -> object:
        """Return the company snapshot or raise if the capability is missing."""
        fn = getattr(self._org, "get_company", None)
        if callable(fn):
            return await fn()
        raise _capability(
            "company_get",
            "OrgMutationService does not expose get_company",
        )

    async def update_company(
        self,
        *,
        payload: Mapping[str, object],
        actor_id: NotBlankStr,
    ) -> object:
        """Apply a partial company-settings update.

        The payload is validated against :class:`UpdateCompanyRequest`
        here so unknown keys are rejected before reaching the mutation
        service.  The call then matches the target signature
        ``update_company(data, *, saved_by=...)`` -- ``actor_id`` is
        recorded as ``saved_by`` in the persisted snapshot.
        """
        fn = getattr(self._org, "update_company", None)
        if not callable(fn):
            raise _capability(
                "company_update",
                "OrgMutationService does not expose update_company",
            )
        data = UpdateCompanyRequest.model_validate(dict(payload))
        result = await fn(data, saved_by=actor_id)
        logger.info(COMPANY_UPDATED_VIA_MCP, actor_id=actor_id)
        return result

    async def list_departments(self) -> Sequence[object]:
        """Return the company's departments."""
        fn = getattr(self._org, "list_departments", None)
        if not callable(fn):
            raise _capability(
                "company_list_departments",
                "OrgMutationService does not expose list_departments",
            )
        return tuple(await fn())

    async def reorder_departments(
        self,
        *,
        department_ids: Sequence[str],
        actor_id: NotBlankStr,
    ) -> None:
        """Apply a new department ordering, auditing the change."""
        fn = getattr(self._org, "reorder_departments", None)
        if not callable(fn):
            raise _capability(
                "company_reorder_departments",
                "OrgMutationService does not expose reorder_departments",
            )
        await fn(department_ids=tuple(department_ids), actor=actor_id)
        logger.info(
            DEPARTMENTS_REORDERED_VIA_MCP,
            actor_id=actor_id,
            count=len(department_ids),
        )

    async def list_versions(self) -> Sequence[object]:
        """Return all company-snapshot versions."""
        fn = getattr(self._org, "list_company_versions", None)
        if not callable(fn):
            raise _capability(
                "company_list_versions",
                "OrgMutationService does not expose list_company_versions",
            )
        return tuple(await fn())

    async def get_version(self, version_id: NotBlankStr) -> object | None:
        """Fetch a single company-snapshot version by id."""
        fn = getattr(self._org, "get_company_version", None)
        if not callable(fn):
            raise _capability(
                "company_get_version",
                "OrgMutationService does not expose get_company_version",
            )
        return cast("object | None", await fn(version_id))


# ── DepartmentService ───────────────────────────────────────────────


class _DepartmentRecord:
    __slots__ = ("created_at", "description", "id", "name", "updated_at")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        description: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.created_at = created_at
        self.updated_at = created_at

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class DepartmentService:
    """Department CRUD + health.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (check-then-act in :meth:`update_department` and
    :meth:`delete_department`).
    """

    def __init__(self) -> None:
        self._departments: dict[UUID, _DepartmentRecord] = {}
        self._lock = asyncio.Lock()

    async def list_departments(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[_DepartmentRecord, ...], int]:
        """Return paginated departments newest-first plus unfiltered total.

        Args:
            offset: Non-negative page offset.
            limit: Optional positive page size; ``None`` returns every
                department from ``offset`` onwards.

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
            snapshot = tuple(copy.deepcopy(d) for d in self._departments.values())
        ordered = tuple(
            sorted(snapshot, key=lambda d: d.created_at, reverse=True),
        )
        total = len(ordered)
        end = total if limit is None else offset + limit
        return ordered[offset:end], total

    async def get_department(
        self,
        department_id: NotBlankStr,
    ) -> _DepartmentRecord | None:
        """Fetch a single department by UUID or ``None`` if not found."""
        try:
            key = UUID(department_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._departments.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_department(
        self,
        *,
        name: NotBlankStr,
        description: NotBlankStr,
        actor_id: NotBlankStr,
    ) -> _DepartmentRecord:
        """Create a department, auditing the event on success."""
        record = _DepartmentRecord(
            id=uuid4(),
            name=name,
            description=description,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._departments[record.id] = record
        logger.info(
            DEPARTMENT_CREATED_VIA_MCP,
            department_id=str(record.id),
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def update_department(
        self,
        *,
        department_id: NotBlankStr,
        actor_id: NotBlankStr,
        name: NotBlankStr | None = None,
        description: NotBlankStr | None = None,
    ) -> _DepartmentRecord | None:
        """Patch a department's ``name`` / ``description`` in place."""
        try:
            key = UUID(department_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._departments.get(key)
            if record is None:
                return None
            if name is not None:
                record.name = name
            if description is not None:
                record.description = description
            record.updated_at = datetime.now(UTC)
            returned = copy.deepcopy(record)
        logger.info(
            DEPARTMENT_UPDATED_VIA_MCP,
            department_id=department_id,
            actor_id=actor_id,
        )
        return returned

    async def delete_department(
        self,
        *,
        department_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove a department; emit the audit event only on real removal."""
        try:
            key = UUID(department_id)
        except ValueError:
            return False
        async with self._lock:
            removed = self._departments.pop(key, None) is not None
        if removed:
            logger.info(
                DEPARTMENT_DELETED_VIA_MCP,
                department_id=department_id,
                actor_id=actor_id,
                reason=reason,
                removed=removed,
            )
        return removed

    async def get_health(
        self,
        department_id: NotBlankStr,
    ) -> Mapping[str, object]:
        """Return a summary health payload for the department."""
        record = await self.get_department(department_id)
        if record is None:
            return {"status": "unknown", "reason": "not_found"}
        return {
            "status": "healthy",
            "department_id": str(record.id),
            "last_update": record.updated_at.isoformat(),
        }


# ── TeamService ─────────────────────────────────────────────────────


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
        """Fetch a single team by UUID or ``None`` if not found."""
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
        """Create a team, auditing the event on success."""
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
        """Remove a team; emit the audit event only on real removal."""
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


# ── RoleVersionService ──────────────────────────────────────────────


class RoleVersionService:
    """Read facade for the role-version snapshot history."""

    def __init__(
        self,
        *,
        org_mutation: OrgMutationService | None = None,
    ) -> None:
        self._org = cast("Any", org_mutation) if org_mutation is not None else None

    async def list_versions(
        self,
        *,
        role_name: NotBlankStr | None = None,
    ) -> Sequence[object]:
        """Return role-snapshot versions, optionally filtered by role name."""
        if self._org is None:
            raise _capability(
                "role_versions_list",
                "OrgMutationService not wired on app_state",
            )
        fn = getattr(self._org, "list_role_versions", None)
        if not callable(fn):
            raise _capability(
                "role_versions_list",
                "OrgMutationService does not expose list_role_versions",
            )
        return tuple(await fn(role_name=role_name))

    async def get_version(
        self,
        version_id: NotBlankStr,
    ) -> object | None:
        """Fetch a single role-snapshot version by id."""
        if self._org is None:
            raise _capability(
                "role_versions_get",
                "OrgMutationService not wired on app_state",
            )
        fn = getattr(self._org, "get_role_version", None)
        if not callable(fn):
            raise _capability(
                "role_versions_get",
                "OrgMutationService does not expose get_role_version",
            )
        return cast("object | None", await fn(version_id))


__all__ = [
    "CompanyReadService",
    "DepartmentService",
    "RoleVersionService",
    "TeamService",
]
