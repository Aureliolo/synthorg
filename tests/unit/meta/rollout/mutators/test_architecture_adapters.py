"""build_architecture_adapters routes restores to the right durable store."""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.core.role import Role
from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.service import WorkflowService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators import build_architecture_adapters
from synthorg.meta.rollout.mutators.architecture_mutator import ArchitectureAdapter
from synthorg.organization.enums import DepartmentName
from synthorg.organization.services import DepartmentService
from synthorg.persistence.role_registry_protocol import RoleRegistryRepository
from tests._shared import FakeClock, as_uuid, mock_of
from tests.unit.engine.workflow.conftest import make_minimal_definition

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _role_record(name: str) -> RoleRecord:
    role = Role(
        name=NotBlankStr(name),
        department=DepartmentName.ENGINEERING,
        required_skills=(NotBlankStr("python"),),
        authority_level=SeniorityLevel.SENIOR,
        tool_access=(NotBlankStr("git"),),
        description=f"Role {name}.",
    )
    return RoleRecord(role=role, is_builtin=False, created_at=_NOW, updated_at=_NOW)


def _build() -> tuple[Mapping[str, ArchitectureAdapter], SimpleNamespace]:
    role_repo = mock_of[RoleRegistryRepository](
        delete=AsyncMock(return_value=True), save=AsyncMock()
    )
    dept_service = mock_of[DepartmentService](
        delete_department=AsyncMock(return_value=True),
        create_department=AsyncMock(),
    )
    workflow_service = mock_of[WorkflowService](update_definition=AsyncMock())
    adapters = build_architecture_adapters(
        role_repo=role_repo,
        department_service=dept_service,
        workflow_service=workflow_service,
        clock=FakeClock(),
    )
    return adapters, SimpleNamespace(
        role=role_repo, dept=dept_service, workflow=workflow_service
    )


class TestRoleAdapter:
    async def test_none_previous_deletes_role(self) -> None:
        adapters, m = _build()
        role_repo = m.role

        await adapters["role"]("bottleneck_specialist", None)

        role_repo.delete.assert_awaited_once()
        assert str(role_repo.delete.call_args.args[0]) == "bottleneck_specialist"
        role_repo.save.assert_not_awaited()

    async def test_mapping_previous_restores_role(self) -> None:
        adapters, m = _build()
        role_repo = m.role
        prior = _role_record("bottleneck_specialist")

        await adapters["role"]("bottleneck_specialist", prior.model_dump(mode="json"))

        role_repo.save.assert_awaited_once()
        saved = role_repo.save.call_args.args[0]
        assert isinstance(saved, RoleRecord)
        assert saved.role.name == "bottleneck_specialist"
        assert saved.role.department is DepartmentName.ENGINEERING
        role_repo.delete.assert_not_awaited()


class TestDepartmentAdapter:
    async def test_none_previous_deletes_department(self) -> None:
        adapters, m = _build()
        dept = m.dept
        dept_id = str(as_uuid("dept-1"))

        await adapters["department"](dept_id, None)

        dept.delete_department.assert_awaited_once()
        assert str(dept.delete_department.call_args.kwargs["department_id"]) == dept_id

    async def test_mapping_previous_recreates_department(self) -> None:
        adapters, m = _build()
        dept = m.dept
        dept_id = as_uuid("dept-1")

        await adapters["department"](
            str(dept_id),
            {"name": "Platform", "description": "Platform team", "id": str(dept_id)},
        )

        dept.create_department.assert_awaited_once()
        kwargs = dept.create_department.call_args.kwargs
        assert str(kwargs["name"]) == "Platform"
        assert kwargs["department_id"] == dept_id

    async def test_non_mapping_previous_rejected(self) -> None:
        adapters, m = _build()
        dept = m.dept

        with pytest.raises(RollbackMutationDeniedError, match="mapping"):
            await adapters["department"]("x", "not-a-mapping")
        dept.create_department.assert_not_awaited()

    async def test_payload_id_target_mismatch_rejected(self) -> None:
        adapters, m = _build()
        dept = m.dept
        other_id = as_uuid("dept-other")

        with pytest.raises(RollbackMutationDeniedError, match="match the operation"):
            await adapters["department"](
                "department-target-tail",
                {
                    "name": "Platform",
                    "description": "Platform team",
                    "id": str(other_id),
                },
            )
        dept.create_department.assert_not_awaited()


class TestWorkflowAdapter:
    async def test_restore_bumps_revision_past_current(self) -> None:
        adapters, m = _build()
        workflow = m.workflow
        prior = make_minimal_definition(name="review_pipeline", revision=1)
        current = make_minimal_definition(name="review_pipeline", revision=5)
        workflow.list_definitions = AsyncMock(return_value=(current,))

        await adapters["workflow"]("review_pipeline", prior.model_dump(mode="json"))

        workflow.update_definition.assert_awaited_once()
        restored = workflow.update_definition.call_args.args[0]
        assert restored.name == "review_pipeline"
        # Revision bumps past the currently-stored revision so the
        # optimistic-concurrency write is accepted.
        assert restored.revision == current.revision + 1

    async def test_restore_uses_prior_revision_when_absent(self) -> None:
        adapters, m = _build()
        workflow = m.workflow
        prior = make_minimal_definition(name="review_pipeline", revision=3)
        workflow.list_definitions = AsyncMock(return_value=())

        await adapters["workflow"]("review_pipeline", prior.model_dump(mode="json"))

        restored = workflow.update_definition.call_args.args[0]
        assert restored.revision == prior.revision + 1

    async def test_none_previous_rejected(self) -> None:
        # There is no workflow-delete rollback path; a None previous_value is a
        # malformed operation and must fail loudly, not raise an opaque error.
        adapters, m = _build()
        workflow = m.workflow

        with pytest.raises(RollbackMutationDeniedError, match="previous definition"):
            await adapters["workflow"]("review_pipeline", None)
        workflow.update_definition.assert_not_awaited()
