"""Unit tests for ``DeliverableReceiptService.build_and_store``.

Exercises the execution-id join and deliverable-slug resolution end to
end with in-memory fakes: the receipt's signals must come from the
records keyed to the run's ``execution_id``, the slug must resolve via
the deliverable doc whose ``related_task_ids`` include the task, and
both silent-``None`` branches (no flight frame, no matching doc) must be
honoured without persisting a receipt.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import (
    DocType,
    SourceStatus,
    SourceType,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.deliverable_receipts.builder import ReceiptBuilder
from synthorg.deliverable_receipts.renderer import ReceiptRenderer
from synthorg.deliverable_receipts.service import DeliverableReceiptService
from synthorg.deliverable_receipts.validator import ReceiptValidator
from synthorg.docs_engine.models import DocMetadata, LivingDocument
from synthorg.docs_engine.service import DocsService
from synthorg.knowledge.models import KnowledgeSource
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
)
from synthorg.persistence.cost_record_protocol import CostRecordRepository
from synthorg.persistence.docs_protocol import DocsRepository
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameAggregate,
    FlightRecorderFrameRepository,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageRecord
from tests._shared import FakeClock, as_uuid, mock_of
from tests.unit.deliverable_receipts._fakes import (
    InMemoryCodeExecutionRecordRepository,
    InMemoryDeliverableReceiptRepository,
    InMemoryKnowledgeUsageRecordRepository,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
_HASH = "a" * 64
_TASK_UUID = as_uuid("t-1")
_TASK_ID = str(_TASK_UUID)
_PROJECT = "p-1"
_EXEC = "exec-1"
_SLUG = "quarterly-report"


def _task() -> Task:
    return Task(
        id=_TASK_UUID,
        title="Deliverable task",
        description="produces a deliverable",
        type=TaskType.DEVELOPMENT,
        project=_PROJECT,
        created_by="alice",
        status=TaskStatus.COMPLETED,
        assigned_to="bob",
    )


def _knowledge_source(source_id: str) -> KnowledgeSource:
    return KnowledgeSource(
        source_id=source_id,
        source_type=SourceType.REPO,
        uri=f"repo://{source_id}",
        title=f"Source {source_id}",
        content_hash=_HASH,
        status=SourceStatus.INDEXED,
        chunk_count=1,
        created_at=_NOW,
        updated_at=_NOW,
        last_indexed_at=_NOW,
    )


def _deliverable_meta(slug: str = _SLUG) -> DocMetadata:
    return DocMetadata(
        project_id=_PROJECT,
        slug=slug,
        doc_type=DocType.DELIVERABLE,
        title="Quarterly Report",
        head_commit_sha="0" * 40,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _deliverable_doc(
    *,
    slug: str = _SLUG,
    related_task_ids: tuple[str, ...] = (_TASK_ID,),
) -> LivingDocument:
    return LivingDocument(
        slug=slug,
        title="Quarterly Report",
        doc_type=DocType.DELIVERABLE,
        related_task_ids=related_task_ids,
        author_agent_id="bob",
        body=(),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _usage(source_id: str, *, execution_id: str = _EXEC) -> KnowledgeUsageRecord:
    return KnowledgeUsageRecord(
        task_id=_TASK_ID,
        execution_id=execution_id,
        project_id=_PROJECT,
        source_id=source_id,
        chunk_id=f"chunk-{source_id}",
        content_hash=_HASH,
    )


def _code_record(*, execution_id: str = _EXEC) -> CodeExecutionRecord:
    return CodeExecutionRecord(
        record_id=f"code-{execution_id}",
        task_id=_TASK_ID,
        execution_id=execution_id,
        project_id=_PROJECT,
        purpose=CodeExecutionPurpose.TESTS,
        command="pytest -q",
        returncode=0,
        passed=True,
        timed_out=False,
        stdout_tail="ok",
        stderr_tail=None,
        executed_at=_NOW,
    )


def _service(
    *,
    usage: InMemoryKnowledgeUsageRecordRepository,
    code: InMemoryCodeExecutionRecordRepository,
    receipts: InMemoryDeliverableReceiptRepository,
    latest_execution_id: str | None = _EXEC,
    doc: LivingDocument | None = None,
) -> DeliverableReceiptService:
    sources = mock_of[KnowledgeSourceRepository]()
    sources.get = AsyncMock(
        spec=KnowledgeSourceRepository.get,
        side_effect=_knowledge_source,
    )
    cost_records = mock_of[CostRecordRepository]()
    cost_records.query = AsyncMock(spec=CostRecordRepository.query, return_value=())
    builder = ReceiptBuilder(
        cost_records=cost_records,
        knowledge_usage_records=usage,
        knowledge_sources=sources,
        code_execution_records=code,
        clock=FakeClock(),
        default_currency="USD",
    )
    flight_recorder = mock_of[FlightRecorderFrameRepository]()
    flight_recorder.get_aggregate = AsyncMock(
        spec=FlightRecorderFrameRepository.get_aggregate,
        return_value=FlightRecorderFrameAggregate(
            latest_execution_id=latest_execution_id,
        ),
    )
    docs = mock_of[DocsRepository]()
    docs.query = AsyncMock(
        spec=DocsRepository.query,
        return_value=(_deliverable_meta(),),
    )
    docs_service = mock_of[DocsService]()
    docs_service.read_doc = AsyncMock(
        spec=DocsService.read_doc,
        return_value=doc if doc is not None else _deliverable_doc(),
    )
    renderer = mock_of[ReceiptRenderer]()
    renderer.render_into_doc = AsyncMock(spec=ReceiptRenderer.render_into_doc)
    return DeliverableReceiptService(
        receipts=receipts,
        builder=builder,
        validator=mock_of[ReceiptValidator](),
        renderer=renderer,
        docs=docs,
        docs_service=docs_service,
        flight_recorder=flight_recorder,
    )


async def test_build_and_store_joins_on_execution_id() -> None:
    usage = InMemoryKnowledgeUsageRecordRepository()
    await usage.append(_usage("s1"))
    code = InMemoryCodeExecutionRecordRepository()
    await code.append(_code_record())
    receipts = InMemoryDeliverableReceiptRepository()
    service = _service(usage=usage, code=code, receipts=receipts)

    receipt = await service.build_and_store(task=_task())

    assert receipt is not None
    assert receipt.execution_id == _EXEC
    assert receipt.deliverable_doc_slug == _SLUG
    assert {s.source_id for s in receipt.sources} == {"s1"}
    assert len(receipt.tests) == 1
    assert receipt.tests[0].passed is True
    # Persisted and retrievable by the controller's read path.
    persisted = await receipts.get(receipt.receipt_id)
    assert persisted is not None


async def test_records_from_other_execution_excluded() -> None:
    usage = InMemoryKnowledgeUsageRecordRepository()
    await usage.append(_usage("s1", execution_id=_EXEC))
    await usage.append(_usage("s-other", execution_id="exec-other"))
    code = InMemoryCodeExecutionRecordRepository()
    await code.append(_code_record(execution_id=_EXEC))
    await code.append(_code_record(execution_id="exec-other"))
    receipts = InMemoryDeliverableReceiptRepository()
    service = _service(usage=usage, code=code, receipts=receipts)

    receipt = await service.build_and_store(task=_task())

    assert receipt is not None
    assert {s.source_id for s in receipt.sources} == {"s1"}
    assert len(receipt.tests) == 1


async def test_no_flight_frame_returns_none() -> None:
    receipts = InMemoryDeliverableReceiptRepository()
    service = _service(
        usage=InMemoryKnowledgeUsageRecordRepository(),
        code=InMemoryCodeExecutionRecordRepository(),
        receipts=receipts,
        latest_execution_id=None,
    )

    receipt = await service.build_and_store(task=_task())

    assert receipt is None
    assert await receipts.list_items() == ()


async def test_no_matching_deliverable_returns_none() -> None:
    receipts = InMemoryDeliverableReceiptRepository()
    service = _service(
        usage=InMemoryKnowledgeUsageRecordRepository(),
        code=InMemoryCodeExecutionRecordRepository(),
        receipts=receipts,
        doc=_deliverable_doc(related_task_ids=("some-other-task",)),
    )

    receipt = await service.build_and_store(task=_task())

    assert receipt is None
    assert await receipts.list_items() == ()
