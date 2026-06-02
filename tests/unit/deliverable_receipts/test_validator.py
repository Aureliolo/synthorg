"""Unit tests for the deliverable-receipt validator."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import SourceStatus, SourceType
from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptCassetteRef,
    ReceiptSourceEntry,
    ReceiptTestEntry,
)
from synthorg.deliverable_receipts.validator import ReceiptValidator
from synthorg.knowledge.models import KnowledgeSource
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
from synthorg.providers.cassette.store import CassetteDocument
from tests._shared import mock_of
from tests.unit.deliverable_receipts._fakes import (
    InMemoryCodeExecutionRecordRepository,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
_HASH = "a" * 64


def _knowledge_source(*, source_id: str, content_hash: str) -> KnowledgeSource:
    return KnowledgeSource(
        source_id=source_id,
        source_type=SourceType.REPO,
        uri="repo://x",
        title="A source",
        content_hash=content_hash,
        status=SourceStatus.INDEXED,
        chunk_count=1,
        created_at=_NOW,
        updated_at=_NOW,
        last_indexed_at=_NOW,
    )


def _sources_get(value: KnowledgeSource | None) -> AsyncMock:
    return AsyncMock(spec=KnowledgeSourceRepository.get, return_value=value)


def _receipt(**overrides: object) -> DeliverableReceipt:
    data: dict[str, object] = {
        "receipt_id": "r-1",
        "task_id": "t-1",
        "project_id": "p-1",
        "execution_id": "exec-1",
        "deliverable_doc_slug": "d",
        "issued_at": _NOW,
        "total_cost": 0.0,
        "currency": "EUR",
    }
    data.update(overrides)
    return DeliverableReceipt.model_validate(data)


def _validator(
    *,
    sources_get: AsyncMock,
    code_records: InMemoryCodeExecutionRecordRepository,
) -> ReceiptValidator:
    knowledge_sources = mock_of[KnowledgeSourceRepository]()
    knowledge_sources.get = sources_get
    return ReceiptValidator(
        knowledge_sources=knowledge_sources,
        code_execution_records=code_records,
    )


def _source_entry(
    *, source_id: str = "s1", content_hash: str = _HASH
) -> ReceiptSourceEntry:
    return ReceiptSourceEntry(
        source_id=source_id,
        chunk_id="c1",
        title="t",
        uri="u",
        content_hash=content_hash,
    )


class TestSourceChecks:
    async def test_resolved_matching_hash_is_valid(self) -> None:
        validator = _validator(
            sources_get=_sources_get(
                _knowledge_source(source_id="s1", content_hash=_HASH),
            ),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        result = await validator.validate(_receipt(sources=(_source_entry(),)))
        assert result.valid is True

    async def test_unresolved_source_fails(self) -> None:
        validator = _validator(
            sources_get=_sources_get(None),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        receipt = _receipt(sources=(_source_entry(source_id="missing"),))
        result = await validator.validate(receipt)
        assert result.valid is False
        assert any("does not resolve" in e for e in result.errors)

    async def test_content_hash_drift_fails(self) -> None:
        validator = _validator(
            sources_get=_sources_get(
                _knowledge_source(source_id="s1", content_hash="b" * 64),
            ),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        result = await validator.validate(_receipt(sources=(_source_entry(),)))
        assert result.valid is False
        assert any("drifted" in e for e in result.errors)


class TestCassetteChecks:
    async def test_valid_cassette_passes(self, tmp_path: Path) -> None:
        path = tmp_path / "cassette.json"
        path.write_text(
            CassetteDocument(cassette_format_version=1).model_dump_json(),
            encoding="utf-8",
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        validator = _validator(
            sources_get=_sources_get(None),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        receipt = _receipt(
            cassette=ReceiptCassetteRef(path=str(path), content_hash=digest),
        )
        result = await validator.validate(receipt)
        assert result.valid is True

    async def test_missing_cassette_fails(self, tmp_path: Path) -> None:
        validator = _validator(
            sources_get=_sources_get(None),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        receipt = _receipt(
            cassette=ReceiptCassetteRef(
                path=str(tmp_path / "absent.json"),
                content_hash=_HASH,
            ),
        )
        result = await validator.validate(receipt)
        assert result.valid is False
        assert any("unreadable" in e for e in result.errors)

    async def test_hash_drift_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "cassette.json"
        path.write_text(
            CassetteDocument(cassette_format_version=1).model_dump_json(),
            encoding="utf-8",
        )
        validator = _validator(
            sources_get=_sources_get(None),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        receipt = _receipt(
            cassette=ReceiptCassetteRef(path=str(path), content_hash=_HASH),
        )
        result = await validator.validate(receipt)
        assert result.valid is False
        assert any("drifted" in e for e in result.errors)


def _code_record(*, returncode: int = 0) -> CodeExecutionRecord:
    return CodeExecutionRecord(
        record_id="rec-1",
        task_id="t-1",
        execution_id="exec-1",
        project_id="p-1",
        purpose=CodeExecutionPurpose.TESTS,
        command="pytest",
        returncode=returncode,
        passed=returncode == 0,
        timed_out=False,
        executed_at=_NOW,
    )


def _test_entry(*, record_id: str = "rec-1", returncode: int = 0) -> ReceiptTestEntry:
    return ReceiptTestEntry(
        record_id=record_id,
        command="pytest",
        returncode=returncode,
        passed=returncode == 0,
        timed_out=False,
        executed_at=_NOW,
    )


class TestTestReconciliation:
    async def test_matching_record_passes(self) -> None:
        records = InMemoryCodeExecutionRecordRepository()
        await records.append(_code_record())
        validator = _validator(sources_get=_sources_get(None), code_records=records)
        result = await validator.validate(_receipt(tests=(_test_entry(),)))
        assert result.valid is True

    async def test_missing_record_fails(self) -> None:
        validator = _validator(
            sources_get=_sources_get(None),
            code_records=InMemoryCodeExecutionRecordRepository(),
        )
        receipt = _receipt(tests=(_test_entry(record_id="ghost"),))
        result = await validator.validate(receipt)
        assert result.valid is False
        assert any("no persisted record" in e for e in result.errors)

    async def test_returncode_mismatch_fails(self) -> None:
        records = InMemoryCodeExecutionRecordRepository()
        await records.append(_code_record(returncode=0))
        validator = _validator(sources_get=_sources_get(None), code_records=records)
        receipt = _receipt(tests=(_test_entry(returncode=1),))
        result = await validator.validate(receipt)
        assert result.valid is False
        assert any("return code mismatch" in e for e in result.errors)
