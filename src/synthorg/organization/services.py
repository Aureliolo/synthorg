# module-kind: service
# ruff: noqa: EM101
"""Organization facades for the MCP handler layer.

Company read/versions, department CRUD + health, team CRUD, and
role-version history.  Writes route through ``org_mutation_service``
where it already owns the flow; other paths use in-memory stores until
durable repositories land.

The file-level ``EM101`` suppression is intentional: every capability
gap in this module raises :class:`CapabilityNotSupportedError` from a
short identifier and operator-readable reason, both string literals by
design so capability telemetry (logged via
:data:`MCP_HANDLER_CAPABILITY_GAP`) has a stable, grep-able message.
Hoisting them to ``msg = ...`` locals would obscure the one-line intent
with no runtime benefit.
"""

import asyncio
import copy
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.actor_context import ActorIdentity, ActorKind, actor_scope
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.company import Company
from synthorg.core.pagination import collect_all
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.company import (
    COMPANY_UPDATED_VIA_MCP,
    DEPARTMENT_CREATED_VIA_MCP,
    DEPARTMENT_DELETED_VIA_MCP,
    DEPARTMENT_UPDATED_VIA_MCP,
    DEPARTMENTS_REORDERED_VIA_MCP,
)
from synthorg.organization.department_record import _DepartmentRecord
from synthorg.organization.models import ReorderDepartmentsRequest, UpdateCompanyRequest
from synthorg.persistence.department_protocol import DepartmentRepository
from synthorg.persistence.version_protocol import VersionRepository
from synthorg.settings.resolver import ConfigResolver

if TYPE_CHECKING:
    from synthorg.api.services.org_mutations import OrgMutationService


logger = get_logger(__name__)

# Company structure is single-tenant, so its version history is keyed under a
# single entity id (matching the REST company-version controller).
_COMPANY_ENTITY_ID = NotBlankStr("default")


# ── CompanyReadService ──────────────────────────────────────────────


class CompanyReadService:
    """Read + light-mutation facade over the company/org surface.

    Reads project the same durable sources the REST company controllers
    use: the config resolver for the company snapshot + departments, and
    the ``VersionRepository[Company]`` for history. Writes route through
    ``OrgMutationService`` (the single owner of the company-settings write
    + snapshot flow). ``company_versions`` is optional: a persistence-less
    deployment has no durable history, so the version reads surface a
    capability gap rather than a 503.
    """

    def __init__(
        self,
        *,
        org_mutation: OrgMutationService,
        config_resolver: ConfigResolver,
        company_versions: VersionRepository[Company] | None = None,
    ) -> None:
        self._org = org_mutation
        self._resolver = config_resolver
        self._versions = company_versions

    async def get_company(self) -> Mapping[str, object]:
        """Return the curated company snapshot (name, agents, departments).

        Returns:
            A JSON-safe mapping mirroring the REST ``GET /company`` shape.
        """
        async with asyncio.TaskGroup() as tg:
            t_name = tg.create_task(self._resolver.get_str("company", "company_name"))
            t_agents = tg.create_task(self._resolver.get_agents())
            t_depts = tg.create_task(self._resolver.get_departments())
        return {
            "company_name": t_name.result(),
            "agents": [a.model_dump(mode="json") for a in t_agents.result()],
            "departments": [d.model_dump(mode="json") for d in t_depts.result()],
        }

    async def update_company(
        self,
        *,
        payload: Mapping[str, object],
        actor_id: NotBlankStr,
    ) -> Mapping[str, object]:
        """Apply a partial company-settings update.

        The payload is validated against :class:`UpdateCompanyRequest`
        here so unknown keys are rejected before reaching the mutation
        service. ``actor_id`` is bound to the actor-context seam for the
        call so the mutation service's snapshot leaf attributes the
        version to this caller (the MCP layer is bypassed by the
        HTTP-only ``AuthContextMiddleware``, so the binding is explicit
        here).

        Returns:
            The updated company fields returned by the mutation service.
        """
        data = UpdateCompanyRequest.model_validate(dict(payload))
        actor = ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, label=actor_id)
        with actor_scope(actor):
            updated, _etag = await self._org.update_company(data)
        logger.info(COMPANY_UPDATED_VIA_MCP, actor_id=actor_id)
        return updated

    async def list_departments(self) -> Sequence[object]:
        """Return the company's departments.

        Returns:
            The department models resolved from company settings.
        """
        return tuple(await self._resolver.get_departments())

    async def reorder_departments(
        self,
        *,
        department_ids: Sequence[str],
        actor_id: NotBlankStr,
    ) -> None:
        """Apply a new department ordering (by name), auditing the change.

        ``department_ids`` is an exact permutation of the current
        department names (the reorder key is the display name, matching
        the REST ``POST /company/reorder-departments`` contract).
        """
        data = ReorderDepartmentsRequest(
            department_names=tuple(NotBlankStr(name) for name in department_ids),
        )
        actor = ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, label=actor_id)
        with actor_scope(actor):
            await self._org.reorder_departments(data)
        logger.info(
            DEPARTMENTS_REORDERED_VIA_MCP,
            actor_id=actor_id,
            count=len(department_ids),
        )

    async def list_versions(self) -> Sequence[object]:
        """Return every company-snapshot version, newest-repo-order.

        Returns:
            All company version snapshots from the durable repository.

        Raises:
            CapabilityNotSupportedError: When no durable version
                repository is wired (a persistence-less deployment).
        """
        if self._versions is None:
            raise CapabilityNotSupportedError(
                "company_versions_list",
                "company version history requires a durable persistence backend",
            )
        versions = self._versions
        return await collect_all(
            lambda limit, offset: versions.list_versions(
                _COMPANY_ENTITY_ID, limit=limit, offset=offset
            )
        )

    async def get_version(self, version_id: NotBlankStr) -> object | None:
        """Fetch a single company-snapshot version by its version number.

        ``version_id`` is the one-based monotonic version number (the only
        stable key company snapshots carry); a non-numeric or out-of-range
        value resolves to ``None`` so the handler maps it onto
        ``not_found``.

        Returns:
            The matching company-snapshot version, or ``None``.

        Raises:
            CapabilityNotSupportedError: When no durable version
                repository is wired (a persistence-less deployment).
        """
        if self._versions is None:
            raise CapabilityNotSupportedError(
                "company_versions_get",
                "company version history requires a durable persistence backend",
            )
        try:
            version_num = int(version_id)
        except ValueError:
            return None
        if version_num < 1:
            return None
        return await self._versions.get_version(_COMPANY_ENTITY_ID, version_num)


# ── DepartmentService ───────────────────────────────────────────────


class DepartmentService:
    """Department CRUD + health.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (check-then-act in :meth:`update_department` and
    :meth:`delete_department`).
    """

    def __init__(
        self,
        *,
        repo: DepartmentRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._departments: dict[UUID, _DepartmentRecord] = {}
        self._lock = asyncio.Lock()
        self._repo = repo
        self._clock = clock if clock is not None else SystemClock()

    async def rehydrate(self) -> None:
        """Load durable departments into the in-memory cache at boot.

        A no-op when no durable repository is wired (the in-memory service
        stands alone). After this the cache mirrors the durable store, so
        reads stay in memory and writes go through to the repository.
        """
        if self._repo is None:
            return
        repo = self._repo
        records = await collect_all(
            lambda limit, offset: repo.list_items(limit=limit, offset=offset)
        )
        async with self._lock:
            self._departments = {
                record.id: _DepartmentRecord.from_durable(record) for record in records
            }

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
        """Fetch a single department by UUID or ``None`` if not found.

        Returns:
            A deep copy of the stored department, or ``None`` when the id
            is malformed or no such department exists.
        """
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
        department_id: UUID | None = None,
    ) -> _DepartmentRecord:
        """Create a department, auditing the event on success.

        Args:
            name: Department display name (UNIQUE in the durable store).
            description: Human-readable description.
            actor_id: Auditing actor identifier.
            department_id: Optional explicit primary key. Supplied by
                rollback paths so a re-created department keeps its
                original id and existing references stay valid; defaults
                to a fresh ``uuid4()``.

        Returns:
            A deep copy of the newly created department record.
        """
        record = _DepartmentRecord(
            id=department_id if department_id is not None else uuid4(),
            name=name,
            description=description,
            created_at=self._clock.now(),
        )
        # Durable write and cache insert are serialised under one lock so the
        # in-memory cache never diverges from the store: a unique-name
        # collision raises here before the cache is mutated.
        async with self._lock:
            if self._repo is not None:
                await self._repo.save(record.to_durable())
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
        """Patch a department's ``name`` / ``description`` in place.

        Returns:
            A deep copy of the updated department, or ``None`` when the id
            is malformed or no such department exists.
        """
        try:
            key = UUID(department_id)
        except ValueError:
            return None
        async with self._lock:
            current = self._departments.get(key)
            if current is None:
                return None
            # Mutate a copy and only commit it to the cache after the durable
            # write succeeds, so a failed save cannot leave the in-memory cache
            # ahead of the store.
            candidate = copy.deepcopy(current)
            if name is not None:
                candidate.name = name
            if description is not None:
                candidate.description = description
            candidate.updated_at = self._clock.now()
            # Durable write stays under the lock so a failed save cannot leave
            # the cache ahead of the store for a concurrent reader.
            if self._repo is not None:
                await self._repo.save(candidate.to_durable())
            self._departments[key] = candidate
            returned = copy.deepcopy(candidate)
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
        """Remove a department; emit the audit event only on real removal.

        Returns:
            ``True`` when a department was removed, ``False`` when the id
            is malformed or no such department exists.
        """
        try:
            key = UUID(department_id)
        except ValueError:
            return False
        async with self._lock:
            removed = key in self._departments
            if removed:
                # Durable delete under the lock and before the cache pop so a
                # failed delete leaves both stores consistent and a concurrent
                # rehydrate cannot resurrect a half-removed row.
                if self._repo is not None:
                    await self._repo.delete(NotBlankStr(str(key)))
                self._departments.pop(key, None)
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


# ── RoleVersionService ──────────────────────────────────────────────


class RoleVersionService:
    """Read facade for the per-role version-snapshot history.

    Role snapshots are keyed by ``(role_name, version)`` in the durable
    ``VersionRepository[Role]`` (there is no global snapshot id), so both
    reads require the role name. ``role_versions`` is optional: a
    persistence-less deployment has no durable history and the reads
    surface a capability gap rather than a 503.
    """

    def __init__(
        self,
        *,
        role_versions: VersionRepository[Role] | None = None,
    ) -> None:
        self._versions = role_versions

    async def list_versions(
        self,
        *,
        role_name: NotBlankStr,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[object, ...], int]:
        """Return a paginated slice of a role's snapshots plus the total.

        Args:
            role_name: The role whose version history to list (required;
                snapshots are per-role in the durable store).
            offset: Non-negative page offset.
            limit: Optional positive page size; ``None`` returns every
                version from ``offset`` onwards.

        Returns:
            A ``(page, total)`` pair where ``total`` counts every version
            of ``role_name`` before pagination is applied.

        Raises:
            CapabilityNotSupportedError: When no durable version
                repository is wired (a persistence-less deployment).
        """
        if self._versions is None:
            raise CapabilityNotSupportedError(
                "role_versions_list",
                "role version history requires a durable persistence backend",
            )
        repo = self._versions
        total = await repo.count_versions(role_name)
        if limit is None:
            everything = await collect_all(
                lambda inner_limit, inner_offset: repo.list_versions(
                    role_name, limit=inner_limit, offset=inner_offset
                )
            )
            page = tuple(everything)[offset:]
        else:
            page = tuple(
                await repo.list_versions(role_name, limit=limit, offset=offset)
            )
        return page, total

    async def get_version(
        self,
        *,
        role_name: NotBlankStr,
        version_id: NotBlankStr,
    ) -> object | None:
        """Fetch a single role snapshot by role name + version number.

        ``version_id`` is the one-based monotonic version number; a
        non-numeric or out-of-range value resolves to ``None`` so the
        handler maps it onto ``not_found``.

        Returns:
            The matching role-snapshot version, or ``None``.

        Raises:
            CapabilityNotSupportedError: When no durable version
                repository is wired (a persistence-less deployment).
        """
        if self._versions is None:
            raise CapabilityNotSupportedError(
                "role_versions_get",
                "role version history requires a durable persistence backend",
            )
        try:
            version_num = int(version_id)
        except ValueError:
            return None
        if version_num < 1:
            return None
        return await self._versions.get_version(role_name, version_num)


__all__ = [
    "CompanyReadService",
    "DepartmentService",
    "RoleVersionService",
]
