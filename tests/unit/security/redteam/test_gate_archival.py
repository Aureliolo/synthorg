"""Unit tests for ``RedTeamGateService`` durable-archive side-effect.

The gate persists every evaluation's merged report + verdict to the
optional cross-process archive so the flight-recorder read surface can
surface the verdict later. The write is fail-OPEN: an archive error
never alters the gate verdict, and a duplicate execution is a benign
no-op.
"""

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.persistence_errors import QueryError
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.role_catalog import RED_TEAM_ROLE_NAME
from synthorg.core.task_enums import Complexity, Stakes
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.red_team import (
    RED_TEAM_REPORT_ARCHIVE_FAILED,
    RED_TEAM_REPORT_EXECUTION_ID_MISMATCH,
)
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import AgentRunner
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from tests._shared import FakeClock, role_holder, staffing_with

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

    async def run(
        self,
        *,
        review_input: RedTeamReviewInput,
        red_teamer: AgentIdentity,
    ) -> ModelConfig | None:
        await self._repo.put(
            execution_id=review_input.execution_id,
            report=self._report,
        )
        # The archive records what RAN, so the double answers with the
        # session's own pair rather than inventing one.
        return red_teamer.model


class _RecordingArchive:
    """Minimal archive double that records every append in memory.

    Append-only and keyless, like both real backends: a row is one attack
    event, so an execution attacked, re-opened and attacked again holds two.
    A double that deduplicated on ``execution_id`` would prove a contract
    neither backend has.
    """

    def __init__(self) -> None:
        self._records: list[RedTeamReportRecord] = []

    async def append(self, record: RedTeamReportRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        filter_spec: RedTeamReportFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        records = tuple(self._records)
        if filter_spec.execution_id is not None:
            records = tuple(
                r for r in records if r.execution_id == filter_spec.execution_id
            )
        return records[offset : offset + limit]

    async def count(self, filter_spec: RedTeamReportFilterSpec) -> int:
        return len(await self.query(filter_spec))

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
        agent_summary="Backend service done.",
        acceptance_criteria=("Login endpoint exposed.",),
        assigned_agent_id="agent-1",
        autonomy=AutonomyLevel.SUPERVISED,
        stakes=Stakes.NORMAL,
        estimated_complexity=Complexity.MEDIUM,
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
        staffing=staffing_with(role_holder("red-teamer-1", role=RED_TEAM_ROLE_NAME)),
        grounding_checker=HeuristicGroundingChecker(),
        report_archive=archive,  # type: ignore[arg-type]  # structural double
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
        staffing=staffing_with(role_holder("red-teamer-1", role=RED_TEAM_ROLE_NAME)),
        grounding_checker=HeuristicGroundingChecker(),
        clock=FakeClock(),
    )
    result = await gate.evaluate(_input())
    assert result.verdict is RedTeamVerdict.BLOCK


async def test_a_re_attacked_execution_keeps_both_rows() -> None:
    """A second attack on one execution supersedes nothing.

    The gate runs again whenever a task is decided, re-opened and decided
    again, against the same recorded frame and so the same execution id.
    Both verdicts are evidence: the archive is the record of what was
    judged, and dropping the later one would leave the verdict that
    actually stood with nothing behind it.
    """
    repo = InMemoryRedTeamReportRepository()
    archive = _RecordingArchive()
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

    result = await gate.evaluate(_input())

    assert result.verdict is RedTeamVerdict.BLOCK
    archived = await archive.query(
        RedTeamReportFilterSpec(execution_id=NotBlankStr("exec-1"))
    )
    assert [record.verdict for record in archived] == [
        RedTeamVerdict.PASS,
        RedTeamVerdict.BLOCK,
    ]


def _stale_report() -> RedTeamReport:
    """A HIGH-finding report stamped with a *different* execution id.

    The runner files this under the queried execution key, but its own
    ``execution_id`` belongs to another run, so the gate must reject it
    as a mismatch rather than act on its (stale) HIGH finding.
    """
    return RedTeamReport(
        execution_id="exec-OTHER",
        task_id="task-1",
        findings=(
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.SECURITY,
                severity=RedTeamSeverity.HIGH,
                description="Hardcoded secret in deliverable.",
                evidence=("api_key = 'sk-live'",),
            ),
        ),
        summary="Stale HIGH defect from another run.",
    )


async def test_execution_id_mismatch_fails_open_and_discards_stale_report() -> None:
    """A report stamped to another execution is discarded, not acted on.

    The completion-path input builder sources ``execution_id`` from
    flight-recorder aggregates, so a report left in the per-execution repo
    under the queried key but belonging to a *different* run is reachable.
    The gate must NOT pass that stale deliverable's verdict through: it
    drops the mismatched report, logs the degradation with
    ``gate_degraded=True``, and falls OPEN to a synthetic INFO finding so a
    bookkeeping mismatch never blocks completion AND never leaks the stale
    HIGH. The fail-OPEN INFO finding makes the aggregate
    ``PASS_WITH_FINDINGS`` (a degraded-but-not-blocking verdict), never
    BLOCK.
    """
    repo = InMemoryRedTeamReportRepository()
    archive = _RecordingArchive()
    gate = _gate(report=_stale_report(), repo=repo, archive=archive)

    with structlog.testing.capture_logs() as logs:
        result = await gate.evaluate(_input())

    assert result.verdict is not RedTeamVerdict.BLOCK
    assert result.verdict is RedTeamVerdict.PASS_WITH_FINDINGS
    assert not any(
        f.description == "Hardcoded secret in deliverable."
        for f in result.report.findings
    )
    assert all(f.severity is RedTeamSeverity.INFO for f in result.report.findings)
    mismatch = [e for e in logs if e["event"] == RED_TEAM_REPORT_EXECUTION_ID_MISMATCH]
    assert len(mismatch) == 1
    assert mismatch[0]["gate_degraded"] is True
    assert mismatch[0]["stored_execution_id"] == "exec-OTHER"
    assert mismatch[0]["expected_execution_id"] == "exec-1"
