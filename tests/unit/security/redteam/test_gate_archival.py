"""Unit tests for ``RedTeamGateService`` durable-archive side-effect.

The gate persists every evaluation's merged report + verdict to the
optional cross-process archive so the flight-recorder read surface can
surface the verdict later. The write is fail-OPEN: an archive error
never alters the gate verdict, and a duplicate execution is a benign
no-op.
"""

import pytest
import structlog.testing

from synthorg.core.enums import AutonomyLevel
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability.events.red_team import (
    RED_TEAM_REPORT_ALREADY_ARCHIVED,
    RED_TEAM_REPORT_ARCHIVE_FAILED,
)
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamReviewInput,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import AgentRunner
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


class _ScriptedRunner:
    """``AgentRunner`` test double that writes a pre-built report."""

    def __init__(
        self,
        *,
        repo: InMemoryRedTeamReportRepository,
        report: RedTeamReport,
    ) -> None:
        self._repo = repo
        self._report = report

    async def run(self, *, review_input: RedTeamReviewInput) -> None:
        await self._repo.put(
            execution_id=review_input.execution_id,
            report=self._report,
        )


class _RecordingArchive:
    """Minimal archive double that records every append in memory."""

    def __init__(self) -> None:
        self._records: dict[str, RedTeamReportRecord] = {}

    async def append(self, record: RedTeamReportRecord) -> None:
        if record.execution_id in self._records:
            msg = f"already archived {record.execution_id!r}"
            raise DuplicateRecordError(msg)
        self._records[record.execution_id] = record

    async def query(
        self,
        filter_spec: RedTeamReportFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        records = tuple(self._records.values())
        if filter_spec.execution_id is not None:
            records = tuple(
                r for r in records if r.execution_id == filter_spec.execution_id
            )
        return records[offset : offset + limit]

    async def purge_before(self, threshold: object) -> int:
        del threshold
        return 0


class _RaisingArchive:
    """Archive double whose append always raises ``QueryError``."""

    def __init__(self) -> None:
        self.append_calls = 0

    async def append(self, record: RedTeamReportRecord) -> None:
        del record
        self.append_calls += 1
        msg = "archive backend unavailable"
        raise QueryError(msg)

    async def query(
        self,
        filter_spec: RedTeamReportFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, threshold: object) -> int:
        del threshold
        return 0


def _input() -> RedTeamReviewInput:
    return RedTeamReviewInput(
        task_id="task-1",
        execution_id="exec-1",
        deliverable_content="Backend service done.",
        acceptance_criteria=("Login endpoint exposed.",),
        assigned_agent_id="agent-1",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _high_finding_report() -> RedTeamReport:
    return RedTeamReport(
        execution_id="exec-1",
        task_id="task-1",
        findings=(
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.SECURITY,
                severity=RedTeamSeverity.HIGH,
                description="Hardcoded secret in deliverable.",
                evidence=("api_key = 'sk-live'",),
            ),
        ),
        summary="One HIGH defect.",
    )


def _clean_report() -> RedTeamReport:
    return RedTeamReport(
        execution_id="exec-1",
        task_id="task-1",
        summary="Clean deliverable.",
    )


def _gate(
    *,
    report: RedTeamReport,
    repo: InMemoryRedTeamReportRepository,
    archive: object,
) -> RedTeamGateService:
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=report)
    return RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=HeuristicGroundingChecker(),
        report_archive=archive,  # type: ignore[arg-type] -- structural double
        clock=FakeClock(),
    )


async def test_block_verdict_is_archived() -> None:
    """A BLOCK evaluation persists the merged report + verdict."""
    repo = InMemoryRedTeamReportRepository()
    archive = _RecordingArchive()
    gate = _gate(report=_high_finding_report(), repo=repo, archive=archive)

    result = await gate.evaluate(_input())

    assert result.verdict is RedTeamVerdict.BLOCK
    stored = await archive.query(RedTeamReportFilterSpec(execution_id="exec-1"))
    assert len(stored) == 1
    assert stored[0].verdict is RedTeamVerdict.BLOCK
    assert stored[0].task_id == "task-1"
    assert stored[0].report.findings[0].severity is RedTeamSeverity.HIGH


async def test_pass_verdict_is_archived() -> None:
    """A PASS evaluation is archived too (audit trail covers clean runs)."""
    repo = InMemoryRedTeamReportRepository()
    archive = _RecordingArchive()
    gate = _gate(report=_clean_report(), repo=repo, archive=archive)

    result = await gate.evaluate(_input())

    assert result.verdict is RedTeamVerdict.PASS
    stored = await archive.query(RedTeamReportFilterSpec(execution_id="exec-1"))
    assert len(stored) == 1
    assert stored[0].verdict is RedTeamVerdict.PASS


async def test_archive_failure_does_not_break_verdict() -> None:
    """A failing archive write is fail-OPEN: the verdict still stands."""
    repo = InMemoryRedTeamReportRepository()
    archive = _RaisingArchive()
    gate = _gate(report=_high_finding_report(), repo=repo, archive=archive)

    with structlog.testing.capture_logs() as logs:
        result = await gate.evaluate(_input())

    assert result.verdict is RedTeamVerdict.BLOCK
    assert archive.append_calls == 1
    assert any(e["event"] == RED_TEAM_REPORT_ARCHIVE_FAILED for e in logs)


async def test_no_archive_is_a_noop() -> None:
    """With no archive wired the gate still returns its verdict."""
    repo = InMemoryRedTeamReportRepository()
    gate = RedTeamGateService(
        agent_runner=_ScriptedRunner(repo=repo, report=_high_finding_report()),
        report_repo=repo,
        grounding_checker=HeuristicGroundingChecker(),
        clock=FakeClock(),
    )
    result = await gate.evaluate(_input())
    assert result.verdict is RedTeamVerdict.BLOCK


async def test_duplicate_archive_is_benign() -> None:
    """An archive that already holds the execution is a benign no-op.

    A pre-existing record for the execution (e.g. a retried gate run for
    the same execution id) makes ``append`` raise
    :class:`DuplicateRecordError`; the gate swallows it at DEBUG and the
    verdict is unaffected.
    """
    repo = InMemoryRedTeamReportRepository()
    archive = _RecordingArchive()
    # Pre-seed the archive so the gate's own append hits the duplicate path.
    await archive.append(
        RedTeamReportRecord(
            execution_id="exec-1",
            task_id="task-1",
            verdict=RedTeamVerdict.PASS,
            report=_clean_report(),
            recorded_at=FakeClock().now(),
        )
    )
    gate = _gate(report=_high_finding_report(), repo=repo, archive=archive)

    with structlog.testing.capture_logs() as logs:
        result = await gate.evaluate(_input())

    assert result.verdict is RedTeamVerdict.BLOCK
    archived = [e for e in logs if e["event"] == RED_TEAM_REPORT_ALREADY_ARCHIVED]
    assert any(e.get("note") == "already archived for this execution" for e in archived)
