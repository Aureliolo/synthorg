"""Unit tests for ``BrownfieldEntryAdapter`` and its factory builder."""

import pytest

from synthorg.core.enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.models import (
    CodebaseImportResult,
    CodebaseImportSubmission,
)
from synthorg.engine.brownfield.service import BrownfieldImportService
from synthorg.engine.pipeline.entry.brownfield_adapter import BrownfieldEntryAdapter
from synthorg.engine.pipeline.entry.factory import build_brownfield_entry_adapter
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.protocol import WorkPipeline
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _submission() -> CodebaseImportSubmission:
    return CodebaseImportSubmission(
        project_id=NotBlankStr("proj-1"),
        source_ref=NotBlankStr("/src/legacy"),
        title=NotBlankStr("Legacy"),
        requested_by=NotBlankStr("operator"),
    )


def _import_result() -> CodebaseImportResult:
    return CodebaseImportResult(
        project_id=NotBlankStr("proj-1"),
        source_ref=NotBlankStr("/src/legacy"),
        content_hash=NotBlankStr("a" * 64),
        module_count=3,
        dependency_count=2,
        knowledge_source_id=NotBlankStr("src-1"),
    )


def _pipeline_result(work_item: WorkItem) -> WorkPipelineResult:
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id=NotBlankStr("task-1"),
        final_task_status=TaskStatus.COMPLETED,
        phases=(
            WorkPhaseResult(
                phase=NotBlankStr("intake"), success=True, duration_seconds=0.1
            ),
        ),
        total_duration_seconds=0.1,
    )


class TestBrownfieldEntryAdapter:
    async def test_imports_then_drives_analysis_pass(self) -> None:
        import_service = mock_of[BrownfieldImportService]()
        import_service.import_codebase.return_value = _import_result()
        pipeline = mock_of[WorkPipeline]()
        captured: dict[str, WorkItem] = {}

        async def _run(work_item: WorkItem) -> WorkPipelineResult:
            captured["item"] = work_item
            return _pipeline_result(work_item)

        pipeline.run.side_effect = _run

        adapter = BrownfieldEntryAdapter(
            work_pipeline=pipeline,
            import_service=import_service,
        )
        assert adapter.source is WorkSource.BROWNFIELD

        submission = _submission()
        result = await adapter.submit(submission)

        import_service.import_codebase.assert_awaited_once_with(submission)
        item = captured["item"]
        assert item.source is WorkSource.BROWNFIELD
        assert item.task_type is TaskType.ANALYSIS
        assert item.project == "proj-1"
        assert "CODEBASE_ANALYSIS" in item.raw_intent
        assert result.final_task_status is TaskStatus.COMPLETED

    def test_factory_builds_adapter(self) -> None:
        adapter = build_brownfield_entry_adapter(
            work_pipeline=mock_of[WorkPipeline](),
            import_service=mock_of[BrownfieldImportService](),
        )
        assert isinstance(adapter, BrownfieldEntryAdapter)
        assert adapter.source is WorkSource.BROWNFIELD
