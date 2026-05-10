"""Tests for WorkflowService.validate_definition()."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType, WorkflowType
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.service import WorkflowService
from synthorg.engine.workflow.validation_types import (
    ValidationErrorCode,
    WorkflowValidationResult,
)
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionRepository,
)


def _service() -> WorkflowService:
    from synthorg.persistence.version_protocol import VersionRepository

    return WorkflowService(
        definition_repo=AsyncMock(spec=WorkflowDefinitionRepository),
        version_repo=AsyncMock(spec=VersionRepository),
    )


def _start_end_definition(
    *,
    add_extra_node: bool = False,
    introduce_cycle: bool = False,
) -> WorkflowDefinition:
    nodes: list[WorkflowNode] = [
        WorkflowNode(
            id=NotBlankStr("start"),
            type=WorkflowNodeType.START,
            label=NotBlankStr("Start"),
        ),
        WorkflowNode(
            id=NotBlankStr("end"),
            type=WorkflowNodeType.END,
            label=NotBlankStr("End"),
        ),
    ]
    edges: list[WorkflowEdge] = [
        WorkflowEdge(
            id=NotBlankStr("e1"),
            source_node_id=NotBlankStr("start"),
            target_node_id=NotBlankStr("end"),
            type=WorkflowEdgeType.SEQUENTIAL,
        ),
    ]
    if add_extra_node:
        nodes.append(
            WorkflowNode(
                id=NotBlankStr("orphan"),
                type=WorkflowNodeType.TASK,
                label=NotBlankStr("Orphan"),
                config={"title": "Lonely task"},
            )
        )
    if introduce_cycle:
        edges.append(
            WorkflowEdge(
                id=NotBlankStr("e2"),
                source_node_id=NotBlankStr("end"),
                target_node_id=NotBlankStr("start"),
                type=WorkflowEdgeType.SEQUENTIAL,
            )
        )
    return WorkflowDefinition(
        id=NotBlankStr("wfdef-test001"),
        name=NotBlankStr("Test workflow"),
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr("1.0.0"),
        nodes=tuple(nodes),
        edges=tuple(edges),
        created_by=NotBlankStr("test"),
        created_at=datetime(2026, 4, 25, tzinfo=UTC),
        updated_at=datetime(2026, 4, 25, tzinfo=UTC),
    )


class TestValidateDefinition:
    @pytest.mark.unit
    async def test_valid_definition(self) -> None:
        service = _service()
        definition = _start_end_definition()
        result = await service.validate_definition(definition)
        assert isinstance(result, WorkflowValidationResult)
        assert result.valid is True
        assert result.errors == ()

    @pytest.mark.unit
    async def test_unreachable_node_flagged(self) -> None:
        service = _service()
        definition = _start_end_definition(add_extra_node=True)
        result = await service.validate_definition(definition)
        assert result.valid is False
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.UNREACHABLE_NODE in codes

    @pytest.mark.unit
    async def test_cycle_flagged(self) -> None:
        service = _service()
        definition = _start_end_definition(introduce_cycle=True)
        result = await service.validate_definition(definition)
        assert result.valid is False
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.CYCLE_DETECTED in codes


# Unused-import silencer for static analysis.
_ = NotBlankStr
