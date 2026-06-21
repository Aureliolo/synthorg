"""build_architecture_adapters routes restores to the right durable store."""

from collections.abc import Mapping
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.engine.workflow.service import WorkflowService
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators import build_architecture_adapters
from synthorg.meta.rollout.mutators.architecture_mutator import ArchitectureAdapter
from synthorg.organization.services import DepartmentService
from synthorg.persistence.role_registry_protocol import RoleRegistryRepository
from tests._shared import FakeClock, as_uuid, mock_of
from tests.unit.engine.workflow.conftest import make_minimal_definition

pytestmark = pytest.mark.unit


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
