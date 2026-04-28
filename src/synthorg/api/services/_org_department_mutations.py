"""Department-level mutations for ``OrgMutationService``.

Extracted from ``org_mutations.py`` to keep that module focused on
the service orchestration.
"""

import json
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any

from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.config.schema import AgentConfig  # noqa: TC001
from synthorg.core.company import Department
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    VersionConflictError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_CONCURRENCY_CONFLICT,
    API_DEPARTMENT_CREATED,
    API_DEPARTMENT_DELETED,
    API_DEPARTMENT_UPDATED,
    API_DEPARTMENTS_REORDERED,
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
)

if TYPE_CHECKING:
    from synthorg.api.dto_org import (
        CreateDepartmentRequest,
        ReorderDepartmentsRequest,
        UpdateDepartmentRequest,
    )

logger = get_logger(__name__)

_MAX_CAS_ATTEMPTS = 2


class OrgDepartmentMutationsMixin:
    """Department CRUD + reorder for ``OrgMutationService``."""

    _settings: Any

    async def _read_setting_versioned(  # pragma: no cover - see concrete
        self, namespace: str, key: str
    ) -> tuple[str, str]:
        raise NotImplementedError

    async def _read_departments(  # pragma: no cover - see concrete
        self,
    ) -> tuple[Department, ...]:
        raise NotImplementedError

    async def _read_agents(self) -> tuple[AgentConfig, ...]:  # pragma: no cover
        raise NotImplementedError

    async def _write_departments(  # pragma: no cover - see concrete
        self,
        departments: tuple[Department, ...],
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def _snapshot_company(self, saved_by: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def _find_department(  # pragma: no cover - see concrete
        self, departments: tuple[Department, ...], name: str
    ) -> Department | None:
        raise NotImplementedError

    def _check_budget_sum(  # pragma: no cover - see concrete
        self, departments: tuple[Department, ...]
    ) -> None:
        raise NotImplementedError

    def _collect_department_updates(  # pragma: no cover - see concrete
        self, data: UpdateDepartmentRequest
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _validate_permutation(  # pragma: no cover - see concrete
        self,
        current_names: tuple[str, ...],
        requested_names: tuple[str, ...],
        entity: str,
    ) -> None:
        raise NotImplementedError

    async def create_department(
        self,
        data: CreateDepartmentRequest,
        *,
        saved_by: str = "api",
    ) -> Department:
        """Create a new department."""
        for attempt in range(_MAX_CAS_ATTEMPTS):
            try:
                _, version = await self._read_setting_versioned(
                    "company",
                    "departments",
                )
                departments = await self._read_departments()
                if self._find_department(departments, data.name):
                    msg = f"Department {data.name!r} already exists"
                    logger.warning(
                        API_RESOURCE_CONFLICT,
                        reason=msg,
                        department=data.name,
                    )
                    raise ConflictError(msg)

                dept = Department(
                    name=data.name,
                    head=data.head,
                    budget_percent=data.budget_percent,
                    autonomy_level=data.autonomy_level,
                )
                new_departments = (*departments, dept)
                self._check_budget_sum(new_departments)
                await self._write_departments(
                    new_departments,
                    expected_updated_at=version,
                )
                await self._snapshot_company(saved_by=saved_by)
                break
            except VersionConflictError:
                if attempt == _MAX_CAS_ATTEMPTS - 1:
                    logger.warning(
                        API_CONCURRENCY_CONFLICT,
                        resource="org_mutation",
                        attempts=_MAX_CAS_ATTEMPTS,
                    )
                    raise
                logger.debug(
                    API_CONCURRENCY_CONFLICT,
                    resource="org_mutation",
                    attempt=attempt + 1,
                    max_attempts=_MAX_CAS_ATTEMPTS,
                )
                continue

        logger.info(
            API_DEPARTMENT_CREATED,
            department=data.name,
            budget_percent=data.budget_percent,
        )
        return dept

    async def update_department(
        self,
        name: str,
        data: UpdateDepartmentRequest,
        *,
        if_match: str | None = None,
        saved_by: str = "api",
    ) -> Department:
        """Update an existing department."""
        for attempt in range(_MAX_CAS_ATTEMPTS):
            try:
                _, version = await self._read_setting_versioned(
                    "company",
                    "departments",
                )
                departments = await self._read_departments()
                existing = self._find_department(departments, name)
                if existing is None:
                    msg = f"Department {name!r} not found"
                    logger.warning(
                        API_RESOURCE_NOT_FOUND,
                        reason=msg,
                        department=name,
                    )
                    raise NotFoundError(msg)

                if if_match:
                    cur = json.dumps(
                        existing.model_dump(mode="json"),
                        sort_keys=True,
                    )
                    check_if_match(
                        if_match,
                        compute_etag(cur, ""),
                        f"department:{name}",
                    )

                updates = self._collect_department_updates(data)
                updated = existing.model_copy(update=updates, deep=True)
                new_departments = tuple(
                    updated if d.name.lower() == name.lower() else d
                    for d in departments
                )
                self._check_budget_sum(new_departments)
                await self._write_departments(
                    new_departments,
                    expected_updated_at=version,
                )
                await self._snapshot_company(saved_by=saved_by)
                break
            except VersionConflictError:
                if attempt == _MAX_CAS_ATTEMPTS - 1:
                    logger.warning(
                        API_CONCURRENCY_CONFLICT,
                        resource="org_mutation",
                        attempts=_MAX_CAS_ATTEMPTS,
                    )
                    raise
                logger.debug(
                    API_CONCURRENCY_CONFLICT,
                    resource="org_mutation",
                    attempt=attempt + 1,
                    max_attempts=_MAX_CAS_ATTEMPTS,
                )
                continue

        logger.info(
            API_DEPARTMENT_UPDATED,
            department=name,
            updated_fields=list(updates.keys()),
        )
        return updated

    async def delete_department(
        self,
        name: str,
        *,
        saved_by: str = "api",
    ) -> None:
        """Delete a department."""
        for attempt in range(_MAX_CAS_ATTEMPTS):
            try:
                await self._try_delete_department(name, saved_by=saved_by)
                break
            except VersionConflictError:
                if attempt == _MAX_CAS_ATTEMPTS - 1:
                    logger.warning(
                        API_CONCURRENCY_CONFLICT,
                        resource="org_mutation",
                        attempts=_MAX_CAS_ATTEMPTS,
                    )
                    raise
                logger.debug(
                    API_CONCURRENCY_CONFLICT,
                    resource="org_mutation",
                    attempt=attempt + 1,
                    max_attempts=_MAX_CAS_ATTEMPTS,
                )
                continue

        logger.info(API_DEPARTMENT_DELETED, department=name)

    async def _try_delete_department(
        self,
        name: str,
        *,
        saved_by: str,
    ) -> None:
        """One TOCTOU-safe attempt: CAS on BOTH departments and agents."""
        from synthorg.api.services._org_serialization import (  # noqa: PLC0415
            json_dump_models as _json_dump_models,
        )

        _, dept_version = await self._read_setting_versioned("company", "departments")
        _, agents_version = await self._read_setting_versioned("company", "agents")
        departments = await self._read_departments()
        if self._find_department(departments, name) is None:
            msg = f"Department {name!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, reason=msg, department=name)
            raise NotFoundError(msg)

        agents = await self._read_agents()
        self._check_no_attached_agents(name, agents)

        new_departments = tuple(
            d for d in departments if d.name.lower() != name.lower()
        )
        await self._settings.set_many(
            [
                ("company", "departments", _json_dump_models(new_departments)),
                ("company", "agents", _json_dump_models(agents)),
            ],
            expected_updated_at_map={
                ("company", "departments"): dept_version,
                ("company", "agents"): agents_version,
            },
        )
        await self._snapshot_company(saved_by=saved_by)

    def _check_no_attached_agents(
        self,
        department_name: str,
        agents: Sequence[AgentConfig],
    ) -> None:
        """Raise ConflictError when any agent references the department."""
        attached = tuple(
            a for a in agents if a.department.lower() == department_name.lower()
        )
        if not attached:
            return
        msg = (
            f"Cannot delete department {department_name!r}: "
            f"{len(attached)} agents attached"
        )
        logger.warning(
            API_RESOURCE_CONFLICT,
            reason=msg,
            department=department_name,
            agent_count=len(attached),
        )
        raise ConflictError(msg)

    async def reorder_departments(
        self,
        data: ReorderDepartmentsRequest,
        *,
        saved_by: str = "api",
    ) -> tuple[Department, ...]:
        """Reorder departments."""
        for attempt in range(_MAX_CAS_ATTEMPTS):
            try:
                _, version = await self._read_setting_versioned(
                    "company",
                    "departments",
                )
                departments = await self._read_departments()
                current_names = tuple(d.name for d in departments)
                self._validate_permutation(
                    current_names,
                    data.department_names,
                    "department",
                )

                dept_by_lower = {d.name.lower(): d for d in departments}
                reordered = tuple(
                    dept_by_lower[n.lower()] for n in data.department_names
                )
                await self._write_departments(
                    reordered,
                    expected_updated_at=version,
                )
                await self._snapshot_company(saved_by=saved_by)
                break
            except VersionConflictError:
                if attempt == _MAX_CAS_ATTEMPTS - 1:
                    logger.warning(
                        API_CONCURRENCY_CONFLICT,
                        resource="org_mutation",
                        attempts=_MAX_CAS_ATTEMPTS,
                    )
                    raise
                logger.debug(
                    API_CONCURRENCY_CONFLICT,
                    resource="org_mutation",
                    attempt=attempt + 1,
                    max_attempts=_MAX_CAS_ATTEMPTS,
                )
                continue

        logger.info(
            API_DEPARTMENTS_REORDERED,
            order=[d.name for d in reordered],
        )
        return reordered
