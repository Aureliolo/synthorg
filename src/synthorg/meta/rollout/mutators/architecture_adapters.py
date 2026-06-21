"""Architecture-restore adapters for the rollback executor.

Builds the per-type adapter callables the :class:`RoutedArchitectureMutator`
dispatches by target prefix (``role`` / ``department`` / ``workflow``). Each
adapter reverses the matching architecture apply through its durable store, so
a materialised ``revert_architecture`` operation (emitted by the architecture
applier with an apply-time-captured ``previous_value``) restores org structure
on an auto-rollback. A ``None`` ``previous_value`` means "the apply created
this entity, so delete it"; a non-null value carries the prior state to restore.
"""

from uuid import UUID

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.service import WorkflowService
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators.architecture_mutator import ArchitectureAdapter
from synthorg.organization.services import DepartmentService
from synthorg.persistence.role_registry_protocol import RoleRegistryRepository

_ACTOR = NotBlankStr("meta-loop")


def build_architecture_adapters(
    *,
    role_repo: RoleRegistryRepository,
    department_service: DepartmentService,
    workflow_service: WorkflowService,
    clock: Clock | None = None,
) -> dict[str, ArchitectureAdapter]:
    """Build the role / department / workflow restore adapters.

    Returns:
        Mapping of target-type prefix to its restore adapter callable.
    """
    resolved_clock = clock if clock is not None else SystemClock()

    async def _role_adapter(target_tail: str, previous_value: object) -> None:
        name = NotBlankStr(target_tail)
        if previous_value is None:
            await role_repo.delete(name)
            return
        await role_repo.save(RoleRecord.model_validate(previous_value))

    async def _department_adapter(target_tail: str, previous_value: object) -> None:
        if previous_value is None:
            await department_service.delete_department(
                department_id=NotBlankStr(target_tail),
                actor_id=_ACTOR,
                reason=NotBlankStr("rollback"),
            )
            return
        if not isinstance(previous_value, dict):
            msg = "department restore requires a mapping previous_value"
            raise RollbackMutationDeniedError(msg)
        await department_service.create_department(
            name=NotBlankStr(str(previous_value["name"])),
            description=NotBlankStr(str(previous_value["description"])),
            actor_id=_ACTOR,
            department_id=UUID(str(previous_value["id"])),
        )

    async def _workflow_adapter(target_tail: str, previous_value: object) -> None:
        prior = WorkflowDefinition.model_validate(previous_value)
        current = await _workflow_by_name(workflow_service, target_tail)
        base_revision = current.revision if current is not None else prior.revision
        restored = prior.model_copy(
            update={
                "revision": base_revision + 1,
                "updated_at": resolved_clock.now(),
            }
        )
        await workflow_service.update_definition(restored, saved_by="meta-loop")

    return {
        "role": _role_adapter,
        "department": _department_adapter,
        "workflow": _workflow_adapter,
    }


async def _workflow_by_name(
    service: WorkflowService, name: str
) -> WorkflowDefinition | None:
    """Return the durable workflow definition named ``name``, or ``None``.

    Returns:
        The matching definition, or ``None`` when no workflow has that name.
    """
    for definition in await service.list_definitions():
        if definition.name == name:
            return definition
    return None
