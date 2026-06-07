"""Unit tests for the deliverable-receipt builder."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import SourceStatus, SourceType, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.deliverable_receipts.builder import ReceiptBuilder
from synthorg.knowledge.models import KnowledgeSource
from synthorg.persistence.cost_record_protocol import CostRecordRepository
from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageRecord
from synthorg.providers.cassette.mode import CassetteConfig, CassetteMode
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from tests._shared import FakeClock, as_uuid, mock_of
from tests.unit.deliverable_receipts._fakes import (
    InMemoryCodeExecutionRecordRepository,
    InMemoryKnowledgeUsageRecordRepository,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
_HASH = "a" * 64


def _task() -> Task:
    return Task(
        id=as_uuid("t-1"),
        title="Deliverable task",
        description="produces a deliverable",
        type=TaskType.DEVELOPMENT,
        project="p-1",
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


def _builder(
    *,
    usage: InMemoryKnowledgeUsageRecordRepository,
    code: InMemoryCodeExecutionRecordRepository,
    sources_get: AsyncMock,
    redteam_reports: InMemoryRedTeamReportRepository | None = None,
    cassette_config: CassetteConfig | None = None,
) -> ReceiptBuilder:
    cost_records = mock_of[CostRecordRepository]()
    cost_records.query = AsyncMock(spec=CostRecordRepository.query, return_value=())
    knowledge_sources = mock_of[KnowledgeSourceRepository]()
    knowledge_sources.get = sources_get
    return ReceiptBuilder(
        cost_records=cost_records,
        knowledge_usage_records=usage,
        knowledge_sources=knowledge_sources,
        code_execution_records=code,
        clock=FakeClock(),
        default_currency="EUR",
        redteam_reports=redteam_reports,
        cassette_config=cassette_config,
    )


async def test_empty_run_yields_valid_empty_receipt() -> None:
    builder = _builder(
        usage=InMemoryKnowledgeUsageRecordRepository(),
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(spec=KnowledgeSourceRepository.get, return_value=None),
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert receipt.sources == ()
    assert receipt.decisions == ()
    assert receipt.tests == ()
    assert receipt.red_team is None
    assert receipt.cassette is None
    assert receipt.total_cost == 0.0
    assert receipt.currency == "EUR"


async def test_sources_deduped_and_resolved() -> None:
    usage = InMemoryKnowledgeUsageRecordRepository()
    for chunk, source in (("c1", "s1"), ("c2", "s1"), ("c3", "s2")):
        await usage.append(
            KnowledgeUsageRecord(
                task_id="t-1",
                execution_id="exec-1",
                project_id="p-1",
                source_id=source,
                chunk_id=chunk,
                content_hash=_HASH,
            ),
        )
    builder = _builder(
        usage=usage,
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(
            spec=KnowledgeSourceRepository.get,
            side_effect=_knowledge_source,
        ),
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert {s.source_id for s in receipt.sources} == {"s1", "s2"}
    assert all(s.content_hash == _HASH for s in receipt.sources)


async def test_source_hash_is_the_captured_one_not_the_live_registry() -> None:
    # The receipt must carry the hash captured at retrieval time, not the
    # source's current registry hash; otherwise the validator's drift check
    # compares two live values and can never fire.
    captured = "a" * 64
    drifted_live = "b" * 64
    usage = InMemoryKnowledgeUsageRecordRepository()
    await usage.append(
        KnowledgeUsageRecord(
            task_id="t-1",
            execution_id="exec-1",
            project_id="p-1",
            source_id="s1",
            chunk_id="c1",
            content_hash=captured,
        ),
    )

    def _drifted_source(source_id: str) -> KnowledgeSource:
        return KnowledgeSource(
            source_id=source_id,
            source_type=SourceType.REPO,
            uri=f"repo://{source_id}",
            title=f"Source {source_id}",
            content_hash=drifted_live,
            status=SourceStatus.INDEXED,
            chunk_count=1,
            created_at=_NOW,
            updated_at=_NOW,
            last_indexed_at=_NOW,
        )

    builder = _builder(
        usage=usage,
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(
            spec=KnowledgeSourceRepository.get,
            side_effect=_drifted_source,
        ),
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert receipt.sources[0].content_hash == captured


async def test_unresolved_source_kept_with_placeholder() -> None:
    usage = InMemoryKnowledgeUsageRecordRepository()
    await usage.append(
        KnowledgeUsageRecord(
            task_id="t-1",
            execution_id="exec-1",
            project_id="p-1",
            source_id="gone",
            chunk_id="c1",
            content_hash=_HASH,
        ),
    )
    builder = _builder(
        usage=usage,
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(spec=KnowledgeSourceRepository.get, return_value=None),
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert len(receipt.sources) == 1
    assert receipt.sources[0].source_id == "gone"


async def test_red_team_snapshot_when_report_present() -> None:
    reports = InMemoryRedTeamReportRepository()
    await reports.put(
        execution_id="exec-1",
        report=RedTeamReport(
            execution_id="exec-1",
            task_id="t-1",
            findings=(
                RedTeamFinding(
                    attack_surface=RedTeamAttackSurface.SECURITY,
                    severity=RedTeamSeverity.HIGH,
                    description="leak",
                    evidence=("quote",),
                ),
                RedTeamFinding(
                    attack_surface=RedTeamAttackSurface.CORRECTNESS,
                    severity=RedTeamSeverity.LOW,
                    description="nit",
                ),
            ),
            summary="one high, one low",
        ),
    )
    builder = _builder(
        usage=InMemoryKnowledgeUsageRecordRepository(),
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(spec=KnowledgeSourceRepository.get, return_value=None),
        redteam_reports=reports,
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert receipt.red_team is not None
    assert receipt.red_team.finding_count == 2
    assert receipt.red_team.high_plus_count == 1
    assert receipt.red_team.verdict is RedTeamVerdict.PASS_WITH_FINDINGS


async def test_red_team_none_when_no_report() -> None:
    builder = _builder(
        usage=InMemoryKnowledgeUsageRecordRepository(),
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(spec=KnowledgeSourceRepository.get, return_value=None),
        redteam_reports=InMemoryRedTeamReportRepository(),
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert receipt.red_team is None


async def test_cassette_hashed_when_present(tmp_path: Path) -> None:
    cassette = tmp_path / "run.json"
    cassette.write_bytes(b'{"cassette_format_version": 1, "interactions": []}')
    builder = _builder(
        usage=InMemoryKnowledgeUsageRecordRepository(),
        code=InMemoryCodeExecutionRecordRepository(),
        sources_get=AsyncMock(spec=KnowledgeSourceRepository.get, return_value=None),
        cassette_config=CassetteConfig(mode=CassetteMode.RECORD, path=cassette),
    )
    receipt = await builder.build(
        task=_task(), execution_id="exec-1", deliverable_doc_slug="d"
    )
    assert receipt.cassette is not None
    assert (
        receipt.cassette.content_hash
        == hashlib.sha256(cassette.read_bytes()).hexdigest()
    )
