# module-kind: adapter
"""Concrete durable applier contexts for the prompt and architecture appliers.

These adapters back the read methods (sync, served from in-memory snapshots
refreshed at boot and after each apply) used by ``dry_run`` and the write
methods (async) used by the real ``apply`` path:

* :class:`DurablePromptApplierContext` reads / writes durable active
  principles through the :class:`ActivePrincipleRepository` and refreshes the
  cached read provider so the prompt build sees applied principles.
* :class:`DurableArchitectureApplierContext` reads / writes the durable role
  registry and department store and modifies workflows through the
  already-durable :class:`WorkflowService`, returning a per-change undo closure
  for reverse-order rollback.

The contexts hold the registry / principle snapshots so the synchronous
dry-run reads never await; :meth:`refresh_snapshot` reloads them.
"""

from collections.abc import Callable
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.normalization import collapse_whitespace_lowercase
from synthorg.core.role import Role
from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import (
    ActivePrinciple,
    PrincipleEvolutionMode,
    ScopeKind,
)
from synthorg.engine.strategy.active_principle_provider import (
    CachedActivePrincipleProvider,
)
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.service import WorkflowService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.meta.appliers._architecture_validators import ArchitectureUndo
from synthorg.meta.models import ArchitectureChange, PromptChange
from synthorg.organization.department_record import DepartmentRecord
from synthorg.organization.enums import DepartmentName
from synthorg.organization.services import DepartmentService
from synthorg.persistence.active_principle_protocol import ActivePrincipleRepository
from synthorg.persistence.role_registry_protocol import RoleRegistryRepository

_ALL_SCOPE: Final[str] = "all"


class MetaArchitectureApplyError(
    ValueError
):  # lint-allow: domain-error-hierarchy -- internal control-flow caught by apply(); never reaches an API boundary  # noqa: E501
    """A durable architecture change could not be applied as specified.

    A plain ``ValueError`` subclass (not a ``DomainError``): it is raised
    inside ``apply_change`` and immediately caught by
    ``ArchitectureApplier.apply``, which rolls back and returns a failure
    ``ApplyResult`` rather than letting the exception reach an API boundary.
    """


class DurablePromptApplierContext:
    """Durable read / write context for :class:`PromptApplier`.

    Args:
        principle_repo: Durable active-principle store (the write target).
        provider: Cached read provider refreshed after each apply.
        role_names: Live accessor for the registered role names (scope
            validation). Bound to the architecture context's snapshot so
            roles created by an architecture apply are visible to prompt
            dry-runs without a process restart.
        department_names: Live accessor for the registered department names.
        clock: Time source for principle timestamps.
    """

    def __init__(
        self,
        *,
        principle_repo: ActivePrincipleRepository,
        provider: CachedActivePrincipleProvider,
        role_names: Callable[[], frozenset[str]],
        department_names: Callable[[], frozenset[str]],
        clock: Clock | None = None,
    ) -> None:
        self._repo = principle_repo
        self._provider = provider
        self._role_names = role_names
        self._department_names = department_names
        self._clock = clock if clock is not None else SystemClock()

    def known_roles(self) -> frozenset[str]:
        """Return all registered role names.

        Returns:
            The current role-name set (read live, not a frozen snapshot).
        """
        return self._role_names()

    def known_departments(self) -> frozenset[str]:
        """Return all registered department names.

        Returns:
            The current department-name set (read live, not a frozen snapshot).
        """
        return self._department_names()

    def existing_principles(self, scope: str) -> frozenset[str]:
        """Return normalised principle texts already active at ``scope``.

        Returns:
            The set of normalised texts at ``scope``.
        """
        return frozenset(
            collapse_whitespace_lowercase(p.principle_text)
            for p in self._provider.snapshot()
            if p.scope == scope
        )

    def scope_overridden(self, scope: str) -> bool:
        """Return True when an ``OVERRIDE`` principle already exists at ``scope``.

        Returns:
            Whether any active principle at ``scope`` is in ``OVERRIDE`` mode.
        """
        return any(
            p.scope == scope and p.evolution_mode is PrincipleEvolutionMode.OVERRIDE
            for p in self._provider.snapshot()
        )

    async def create_principle(self, change: PromptChange) -> str:
        """Persist a durable active principle from ``change``.

        Returns:
            The new principle's id, for reverse-order rollback.

        Raises:
            ValueError: When ``change.target_scope`` resolves to neither
                ``all``, a known role, nor a known department.
        """
        scope = change.target_scope
        if scope == _ALL_SCOPE:
            scope_kind = ScopeKind.ALL
        elif scope in self.known_roles():
            scope_kind = ScopeKind.ROLE
        elif scope in self.known_departments():
            scope_kind = ScopeKind.DEPARTMENT
        else:
            msg = (
                f"Cannot resolve scope {scope!r}: not 'all', a known role, or a "
                "known department. Refusing to widen scope to ALL silently."
            )
            raise ValueError(msg)
        now = self._clock.now()
        principle = ActivePrinciple(
            principle_text=change.principle_text,
            scope=NotBlankStr(scope),
            scope_kind=scope_kind,
            evolution_mode=PrincipleEvolutionMode(change.evolution_mode.value),
            created_at=now,
            updated_at=now,
        )
        await self._repo.save(principle)
        return str(principle.id)

    async def delete_principle(self, principle_id: str) -> None:
        """Delete a previously-created active principle (rollback)."""
        await self._repo.delete(NotBlankStr(principle_id))

    async def refresh_snapshot(self) -> None:
        """Reload the cached read provider after a successful apply."""
        await self._provider.refresh()


class DurableArchitectureApplierContext:
    """Durable read / write context for :class:`ArchitectureApplier`.

    Args:
        role_repo: Durable role registry (create / remove role).
        department_service: Department service writing through to its repo
            (create / remove department) so its cache stays coherent.
        workflow_service: Workflow service (modify workflow, already durable).
        agent_registry: Active-agent registry (computes role / dept in-use).
        clock: Time source for role / principle timestamps.
    """

    def __init__(
        self,
        *,
        role_repo: RoleRegistryRepository,
        department_service: DepartmentService,
        workflow_service: WorkflowService,
        agent_registry: AgentRegistryService,
        clock: Clock | None = None,
    ) -> None:
        self._role_repo = role_repo
        self._departments = department_service
        self._workflows = workflow_service
        self._registry = agent_registry
        self._clock = clock if clock is not None else SystemClock()
        self._role_names: frozenset[str] = frozenset()
        self._department_names: frozenset[str] = frozenset()
        self._workflow_names: frozenset[str] = frozenset()
        self._roles_in_use: frozenset[str] = frozenset()
        self._departments_in_use: frozenset[str] = frozenset()

    def has_role(self, name: str) -> bool:
        """Return True when a role with ``name`` is registered."""
        return name in self._role_names

    def has_department(self, name: str) -> bool:
        """Return True when a department with ``name`` is registered."""
        return name in self._department_names

    def has_workflow(self, name: str) -> bool:
        """Return True when a workflow with ``name`` is registered."""
        return name in self._workflow_names

    def role_in_use(self, name: str) -> bool:
        """Return True when removing the role would dangle references."""
        return name in self._roles_in_use

    def department_in_use(self, name: str) -> bool:
        """Return True when removing the department would dangle references."""
        return name in self._departments_in_use

    def role_names(self) -> frozenset[str]:
        """Return the role-name snapshot (for the prompt context).

        Returns:
            Registered role names.
        """
        return self._role_names

    def department_names(self) -> frozenset[str]:
        """Return the department-name snapshot (for the prompt context).

        Returns:
            Registered department names.
        """
        return self._department_names

    async def apply_change(self, change: ArchitectureChange) -> ArchitectureUndo:
        """Durably apply one architecture change and return its undo.

        Returns:
            A coroutine factory reversing exactly this change.

        Raises:
            MetaArchitectureApplyError: When the change cannot be applied
                (unknown operation, missing/invalid payload field).
        """
        operation = change.operation
        if operation == "create_role":
            return await self._create_role(change)
        if operation == "remove_role":
            return await self._remove_role(change)
        if operation == "create_department":
            return await self._create_department(change)
        if operation == "remove_department":
            return await self._remove_department(change)
        if operation == "modify_workflow":
            return await self._modify_workflow(change)
        msg = f"Unsupported architecture operation: {operation}"
        raise MetaArchitectureApplyError(msg)

    async def refresh_snapshot(self) -> None:
        """Reload every registry snapshot after a successful apply."""
        from synthorg.core.pagination import collect_all  # noqa: PLC0415

        role_repo = self._role_repo
        roles = await collect_all(
            lambda limit, offset: role_repo.list_items(limit=limit, offset=offset)
        )
        self._role_names = frozenset(r.role.name for r in roles)
        depts, _ = await self._departments.list_departments()
        self._department_names = frozenset(d.name for d in depts)
        self._workflow_names = frozenset(await self._workflow_names_now())
        agents = await self._registry.list_active()
        self._roles_in_use = frozenset(str(a.role) for a in agents)
        self._departments_in_use = frozenset(str(a.department) for a in agents)

    async def _workflow_names_now(self) -> tuple[str, ...]:
        definitions = await self._workflows.list_definitions()
        return tuple(d.name for d in definitions)

    async def _create_role(self, change: ArchitectureChange) -> ArchitectureUndo:
        record = self._build_role_record(change)
        await self._role_repo.save(record)
        name = NotBlankStr(record.role.name)

        async def _undo() -> None:
            await self._role_repo.delete(name)

        return _undo

    async def _remove_role(self, change: ArchitectureChange) -> ArchitectureUndo:
        name = NotBlankStr(change.target_name)
        prior = await self._role_repo.get(name)
        if prior is None:
            msg = f"Role not found for removal: {change.target_name}"
            raise MetaArchitectureApplyError(msg)
        await self._role_repo.delete(name)

        async def _undo() -> None:
            await self._role_repo.save(prior)

        return _undo

    async def _create_department(self, change: ArchitectureChange) -> ArchitectureUndo:
        record = await self._departments.create_department(
            name=NotBlankStr(change.target_name),
            description=NotBlankStr(change.description),
            actor_id=NotBlankStr("meta-loop"),
        )
        dept_id = NotBlankStr(str(record.id))

        async def _undo() -> None:
            await self._departments.delete_department(
                department_id=dept_id,
                actor_id=NotBlankStr("meta-loop"),
                reason=NotBlankStr("apply rollback"),
            )

        return _undo

    async def _remove_department(self, change: ArchitectureChange) -> ArchitectureUndo:
        name = change.target_name
        prior = await self._department_by_name(name)
        if prior is None:
            msg = f"Department not found for removal: {name}"
            raise MetaArchitectureApplyError(msg)
        await self._departments.delete_department(
            department_id=NotBlankStr(str(prior.id)),
            actor_id=NotBlankStr("meta-loop"),
            reason=NotBlankStr("architecture apply"),
        )

        async def _undo() -> None:
            await self._departments.create_department(
                name=NotBlankStr(prior.name),
                description=NotBlankStr(prior.description or prior.name),
                actor_id=NotBlankStr("meta-loop"),
                department_id=prior.id,
            )

        return _undo

    async def _modify_workflow(self, change: ArchitectureChange) -> ArchitectureUndo:
        service = self._workflows
        current = await self._workflow_by_name(change.target_name)
        if current is None:
            msg = f"Workflow not found for modification: {change.target_name}"
            raise MetaArchitectureApplyError(msg)
        description = change.payload.get("description")
        new_description = (
            str(description) if description is not None else current.description
        )
        updated = current.model_copy(
            update={
                "description": new_description,
                "revision": current.revision + 1,
                "updated_at": self._clock.now(),
            }
        )
        await service.update_definition(updated, saved_by="meta-loop")

        async def _undo() -> None:
            restored = current.model_copy(
                update={
                    "revision": updated.revision + 1,
                    "updated_at": self._clock.now(),
                }
            )
            await service.update_definition(restored, saved_by="meta-loop")

        return _undo

    async def _department_by_name(self, name: str) -> DepartmentRecord | None:
        depts, _ = await self._departments.list_departments()
        for dept in depts:
            if dept.name == name:
                return dept.to_durable()
        return None

    async def _workflow_by_name(self, name: str) -> WorkflowDefinition | None:
        for definition in await self._workflows.list_definitions():
            if definition.name == name:
                return definition
        return None

    def _build_role_record(self, change: ArchitectureChange) -> RoleRecord:
        payload = change.payload
        dept_raw = payload.get("department")
        if dept_raw is None:
            msg = f"create_role for {change.target_name!r} requires a department"
            raise MetaArchitectureApplyError(msg)
        try:
            department = DepartmentName(str(dept_raw))
        except ValueError as exc:
            msg = f"create_role department {dept_raw!r} is not a known DepartmentName"
            raise MetaArchitectureApplyError(msg) from exc
        authority_raw = payload.get("authority_level")
        authority = (
            SeniorityLevel(str(authority_raw))
            if authority_raw is not None
            else SeniorityLevel.MID
        )
        now = self._clock.now()
        role = Role(
            name=NotBlankStr(change.target_name),
            department=department,
            required_skills=_str_tuple(payload.get("required_skills")),
            authority_level=authority,
            tool_access=_str_tuple(payload.get("tool_access")),
            description=str(payload.get("description", "")),
        )
        return RoleRecord(role=role, is_builtin=False, created_at=now, updated_at=now)


def _str_tuple(value: object) -> tuple[NotBlankStr, ...]:
    """Coerce a payload list field into a tuple of non-blank strings.

    Returns:
        The coerced tuple, or empty when *value* is absent / not a list.
    """
    if not isinstance(value, list):
        return ()
    return tuple(NotBlankStr(str(item)) for item in value if str(item).strip())


__all__ = [
    "DurableArchitectureApplierContext",
    "DurablePromptApplierContext",
    "MetaArchitectureApplyError",
]
