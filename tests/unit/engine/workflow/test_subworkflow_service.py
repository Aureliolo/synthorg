"""Tests for SubworkflowService."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SubworkflowNotFoundError,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)
from synthorg.engine.workflow.subworkflow_service import (
    SubworkflowHasParentsError,
    SubworkflowService,
)


def _make_subdef(
    sub_id: str = "sub-1",
    version: str = "1.0.0",
    *,
    is_subworkflow: bool = True,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=NotBlankStr(sub_id),
        name=NotBlankStr("Inner"),
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr(version),
        is_subworkflow=is_subworkflow,
        inputs=(
            WorkflowIODeclaration(
                name=NotBlankStr("payload"),
                type="string",  # type: ignore[arg-type]
                required=True,
            ),
        ),
        outputs=(),
        nodes=(
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
        ),
        edges=(
            WorkflowEdge(
                id=NotBlankStr("e1"),
                source_node_id=NotBlankStr("start"),
                target_node_id=NotBlankStr("end"),
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by=NotBlankStr("system"),
        created_at=datetime(2026, 4, 25, tzinfo=UTC),
        updated_at=datetime(2026, 4, 25, tzinfo=UTC),
    )


def _summary(sub_id: str, *, name: str = "Inner") -> SubworkflowSummary:
    return SubworkflowSummary(
        subworkflow_id=NotBlankStr(sub_id),
        latest_version=NotBlankStr("1.0.0"),
        name=NotBlankStr(name),
        description="",
        input_count=1,
        output_count=0,
        version_count=1,
    )


def _service(registry: AsyncMock | None = None) -> SubworkflowService:
    return SubworkflowService(
        registry=registry or AsyncMock(),
    )


class TestSubworkflowServiceList:
    @pytest.mark.unit
    async def test_lists_unfiltered(self) -> None:
        registry = AsyncMock()
        registry.list_all.return_value = (
            _summary("b-2"),
            _summary("a-1"),
        )
        service = _service(registry)
        page, total = await service.list_summaries(offset=0, limit=10)
        assert total == 2
        # Sorted by (name, latest_version, subworkflow_id) -- both names
        # are "Inner" so the id tiebreaker rules.
        assert page[0].subworkflow_id == "a-1"
        assert page[1].subworkflow_id == "b-2"

    @pytest.mark.unit
    async def test_query_filters_via_search(self) -> None:
        registry = AsyncMock()
        registry.search.return_value = (_summary("a-1"),)
        service = _service(registry)
        _page, total = await service.list_summaries(offset=0, limit=10, query="ner")
        assert total == 1
        registry.search.assert_awaited_once()

    @pytest.mark.unit
    async def test_invalid_offset_rejected(self) -> None:
        service = _service()
        with pytest.raises(ValueError, match="offset"):
            await service.list_summaries(offset=-1, limit=10)

    @pytest.mark.unit
    async def test_invalid_limit_rejected(self) -> None:
        service = _service()
        with pytest.raises(ValueError, match="limit"):
            await service.list_summaries(offset=0, limit=0)


class TestSubworkflowServiceGet:
    @pytest.mark.unit
    async def test_get_specific_version(self) -> None:
        registry = AsyncMock()
        defn = _make_subdef()
        registry.get.return_value = defn
        service = _service(registry)
        result = await service.get(NotBlankStr("sub-1"), NotBlankStr("1.0.0"))
        assert result.id == "sub-1"

    @pytest.mark.unit
    async def test_resolves_latest_when_version_omitted(self) -> None:
        registry = AsyncMock()
        registry.latest_version.return_value = "1.2.3"
        registry.get.return_value = _make_subdef(version="1.2.3")
        service = _service(registry)
        result = await service.get(NotBlankStr("sub-1"))
        assert result.version == "1.2.3"
        registry.get.assert_awaited_with(NotBlankStr("sub-1"), NotBlankStr("1.2.3"))

    @pytest.mark.unit
    async def test_no_versions_raises_not_found(self) -> None:
        registry = AsyncMock()
        registry.latest_version.return_value = None
        service = _service(registry)
        with pytest.raises(SubworkflowNotFoundError):
            await service.get(NotBlankStr("sub-1"))


class TestSubworkflowServiceCreate:
    @pytest.mark.unit
    async def test_publishes_subworkflow(self) -> None:
        registry = AsyncMock()
        service = _service(registry)
        defn = _make_subdef()
        result = await service.create(defn, saved_by="alice")
        assert result.id == "sub-1"
        registry.register.assert_awaited_once_with(defn)

    @pytest.mark.unit
    async def test_rejects_non_subworkflow(self) -> None:
        service = _service()
        defn = _make_subdef(is_subworkflow=False)
        # Caller-error type: ``ValueError`` (not ``SubworkflowIOError``)
        # so handlers can distinguish "your input is wrong" from
        # registry / storage failures, which keep the IO type.
        with pytest.raises(ValueError, match="is_subworkflow"):
            await service.create(defn, saved_by="alice")


class TestSubworkflowServiceDelete:
    @pytest.mark.unit
    async def test_deletes_when_no_parents(self) -> None:
        registry = AsyncMock()
        registry.find_parents.return_value = ()
        service = _service(registry)
        await service.delete(
            NotBlankStr("sub-1"),
            NotBlankStr("1.0.0"),
            reason="cleanup",
            actor_id="alice",
        )
        registry.delete.assert_awaited_once()

    @pytest.mark.unit
    async def test_blocks_when_parents_pin(self) -> None:
        registry = AsyncMock()
        registry.find_parents.return_value = (
            ParentReference(
                parent_id=NotBlankStr("wf-parent"),
                parent_name=NotBlankStr("Parent flow"),
                pinned_version=NotBlankStr("1.0.0"),
                node_id=NotBlankStr("node-1"),
                parent_type="workflow_definition",
                parent_version=None,
            ),
        )
        service = _service(registry)
        with pytest.raises(SubworkflowHasParentsError) as excinfo:
            await service.delete(
                NotBlankStr("sub-1"),
                NotBlankStr("1.0.0"),
                reason="cleanup",
                actor_id="alice",
            )
        assert excinfo.value.parents
        registry.delete.assert_not_called()


# Silence unused imports when only used as type-checker hints.
_ = Any
