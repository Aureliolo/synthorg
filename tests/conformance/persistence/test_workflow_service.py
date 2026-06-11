"""Conformance tests for :class:`WorkflowService.delete_definition` cascade.

The service is thin on top of the workflow-definition and workflow-
version repositories; this file asserts the cascade contract (delete
removes the definition *and* its version snapshots) across both
backends so the behaviour stays parity-stable.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)
from synthorg.engine.workflow.service import WorkflowService
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot
from tests._shared import as_pk, as_uuid, sid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

_START = WorkflowNode(
    id=NotBlankStr("start"),
    type=WorkflowNodeType.START,
    label=NotBlankStr("Start"),
)
_END = WorkflowNode(
    id=NotBlankStr("end"),
    type=WorkflowNodeType.END,
    label=NotBlankStr("End"),
)


def _definition(
    *,
    definition_id: str = "wf-cascade",
    revision: int = 1,
    inputs: tuple[WorkflowIODeclaration, ...] = (),
    outputs: tuple[WorkflowIODeclaration, ...] = (),
    is_subworkflow: bool = False,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=as_pk(definition_id),
        name=NotBlankStr("Cascade fixture"),
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr("1.0.0"),
        inputs=inputs,
        outputs=outputs,
        is_subworkflow=is_subworkflow,
        nodes=(_START, _END),
        edges=(),
        created_by=NotBlankStr("alice"),
        created_at=_NOW,
        updated_at=_NOW,
        revision=revision,
    )


def _snapshot(
    definition: WorkflowDefinition,
    version: int,
) -> VersionSnapshot[WorkflowDefinition]:
    return VersionSnapshot(
        entity_id=str(definition.id),
        version=version,
        content_hash=NotBlankStr(compute_content_hash(definition)),
        snapshot=definition,
        saved_by=NotBlankStr("alice"),
        saved_at=_NOW,
    )


def _service(backend: PersistenceBackend) -> WorkflowService:
    return WorkflowService(
        definition_repo=backend.workflow_definitions,
        version_repo=backend.workflow_versions,
    )


class TestWorkflowServiceCascade:
    async def test_delete_removes_definition_and_versions(
        self, backend: PersistenceBackend
    ) -> None:
        service = _service(backend)
        definition = _definition()
        await service.create_definition(definition)
        await backend.workflow_versions.save_version(_snapshot(definition, 1))
        await backend.workflow_versions.save_version(
            _snapshot(_definition(revision=2), 2),
        )

        assert (
            await backend.workflow_versions.count_versions(
                sid("wf-cascade"),
            )
            == 2
        )

        deleted = await service.delete_definition(sid("wf-cascade"))
        assert deleted is True

        assert await service.get_definition(sid("wf-cascade")) is None
        assert (
            await backend.workflow_versions.count_versions(
                sid("wf-cascade"),
            )
            == 0
        )

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        service = _service(backend)
        result = await service.delete_definition(sid("ghost"))
        assert result is False

    async def test_delete_when_no_versions_present(
        self, backend: PersistenceBackend
    ) -> None:
        service = _service(backend)
        definition = _definition(definition_id="wf-no-versions")
        await service.create_definition(definition)

        deleted = await service.delete_definition(sid("wf-no-versions"))
        assert deleted is True
        assert await service.get_definition(sid("wf-no-versions")) is None

    async def test_list_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        # Seed non-default inputs / outputs / is_subworkflow so a backend
        # that silently drops any of the PST-1 columns fails this test
        # instead of passing because every value matched the column
        # default. Each decl uses STRING with ``required=False`` +
        # ``default=""`` so the validator accepts the optional case.
        service = _service(backend)
        inputs_a = (
            WorkflowIODeclaration(
                name=NotBlankStr("topic"),
                type=WorkflowValueType.STRING,
                required=False,
                default="",
            ),
        )
        outputs_a = (
            WorkflowIODeclaration(
                name=NotBlankStr("summary"),
                type=WorkflowValueType.STRING,
                required=True,
            ),
        )
        await service.create_definition(
            _definition(
                definition_id="wf-a",
                inputs=inputs_a,
                outputs=outputs_a,
                is_subworkflow=True,
            ),
        )
        await service.create_definition(_definition(definition_id="wf-b"))

        listed = await service.list_definitions()
        ids = {d.id for d in listed}
        assert {as_uuid("wf-a"), as_uuid("wf-b")} <= ids

        fetched = await service.get_definition(sid("wf-a"))
        assert fetched is not None
        assert fetched.id == as_uuid("wf-a")
        assert fetched.inputs == inputs_a
        assert fetched.outputs == outputs_a
        assert fetched.is_subworkflow is True
