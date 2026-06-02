"""In-memory fakes for the deliverable-receipt persistence repositories.

Mirror the three feature-owned repository protocols so
``FakePersistenceBackend`` can expose them without a real database. The
behaviour matches the SQLite/Postgres implementations closely enough for
service-level and API-level tests: upsert-by-task for receipts,
newest-first append-only queries for the capture logs.
"""

from datetime import datetime

from pydantic import AwareDatetime

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.deliverable_receipts.models import DeliverableReceipt
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionRecord,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)
from synthorg.persistence.knowledge_usage_protocol import (
    KnowledgeUsageFilterSpec,
    KnowledgeUsageRecord,
)


class InMemoryDeliverableReceiptRepository:
    """In-memory ``DeliverableReceiptRepository`` (upsert by task_id)."""

    def __init__(self) -> None:
        self._by_receipt: dict[str, DeliverableReceipt] = {}

    async def save(self, entity: DeliverableReceipt) -> None:
        for existing_id, existing in list(self._by_receipt.items()):
            if existing.task_id == entity.task_id:
                del self._by_receipt[existing_id]
        self._by_receipt[entity.receipt_id] = entity

    async def get(self, entity_id: NotBlankStr) -> DeliverableReceipt | None:
        return self._by_receipt.get(entity_id)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._by_receipt.pop(entity_id, None) is not None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeliverableReceipt, ...]:
        ordered = self._ordered(tuple(self._by_receipt.values()))
        return tuple(ordered[offset : offset + limit])

    async def query(
        self,
        filter_spec: DeliverableReceiptFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DeliverableReceipt, ...]:
        matched = self._ordered(self._matching(filter_spec))
        return tuple(matched[offset : offset + limit])

    async def count(self, filter_spec: DeliverableReceiptFilterSpec) -> int:
        return len(self._matching(filter_spec))

    def _matching(
        self, filter_spec: DeliverableReceiptFilterSpec
    ) -> tuple[DeliverableReceipt, ...]:
        return tuple(
            r
            for r in self._by_receipt.values()
            if r.project_id == filter_spec.project_id
            and (filter_spec.task_id is None or r.task_id == filter_spec.task_id)
            and (
                filter_spec.deliverable_doc_slug is None
                or r.deliverable_doc_slug == filter_spec.deliverable_doc_slug
            )
        )

    @staticmethod
    def _ordered(
        receipts: tuple[DeliverableReceipt, ...],
    ) -> list[DeliverableReceipt]:
        return sorted(
            receipts,
            key=lambda r: (r.issued_at, r.receipt_id),
            reverse=True,
        )


class InMemoryKnowledgeUsageRecordRepository:
    """In-memory append-only ``KnowledgeUsageRecordRepository``."""

    def __init__(self) -> None:
        self._records: list[KnowledgeUsageRecord] = []

    async def append(self, record: KnowledgeUsageRecord) -> None:
        if any(r.record_id == record.record_id for r in self._records):
            msg = f"Knowledge usage record {record.record_id!r} already exists"
            raise DuplicateRecordError(msg)
        self._records.append(record)

    async def query(
        self,
        filter_spec: KnowledgeUsageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeUsageRecord, ...]:
        matched = [r for r in self._records if self._matches(r, filter_spec)]
        matched.sort(key=lambda r: (r.recorded_at, r.record_id), reverse=True)
        return tuple(matched[offset : offset + limit])

    @staticmethod
    def _matches(
        record: KnowledgeUsageRecord,
        filter_spec: KnowledgeUsageFilterSpec,
    ) -> bool:
        checks = (
            (filter_spec.execution_id, record.execution_id),
            (filter_spec.task_id, record.task_id),
            (filter_spec.project_id, record.project_id),
            (filter_spec.source_id, record.source_id),
        )
        return all(want is None or got == want for want, got in checks)

    async def purge_before(self, threshold: AwareDatetime) -> int:
        cutoff = normalize_utc(threshold)
        keep = [r for r in self._records if normalize_utc(r.recorded_at) >= cutoff]
        removed = len(self._records) - len(keep)
        self._records = keep
        return removed


class InMemoryCodeExecutionRecordRepository:
    """In-memory append-only ``CodeExecutionRecordRepository``."""

    def __init__(self) -> None:
        self._records: list[CodeExecutionRecord] = []

    async def append(self, record: CodeExecutionRecord) -> None:
        if any(r.record_id == record.record_id for r in self._records):
            msg = f"Code execution record {record.record_id!r} already exists"
            raise DuplicateRecordError(msg)
        self._records.append(record)

    async def query(
        self,
        filter_spec: CodeExecutionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CodeExecutionRecord, ...]:
        matched = [r for r in self._records if self._matches(r, filter_spec)]
        matched.sort(key=lambda r: (r.executed_at, r.record_id), reverse=True)
        return tuple(matched[offset : offset + limit])

    @staticmethod
    def _matches(
        record: CodeExecutionRecord,
        filter_spec: CodeExecutionFilterSpec,
    ) -> bool:
        checks: tuple[tuple[object | None, object], ...] = (
            (filter_spec.execution_id, record.execution_id),
            (filter_spec.task_id, record.task_id),
            (filter_spec.project_id, record.project_id),
            (filter_spec.purpose, record.purpose),
        )
        return all(want is None or got == want for want, got in checks)

    async def purge_before(self, threshold: AwareDatetime) -> int:
        cutoff: datetime = normalize_utc(threshold)
        keep = [r for r in self._records if normalize_utc(r.executed_at) >= cutoff]
        removed = len(self._records) - len(keep)
        self._records = keep
        return removed
