"""Unit tests for the Layer 2 peer-review gate (fail-CLOSED orchestration)."""

from datetime import date, datetime
from typing import override
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.persistence_errors import QueryError
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.engine.completion_oracle.errors import CompletionOracleDispatchError
from synthorg.engine.completion_oracle.gate import CompletionOracleGateService
from synthorg.engine.completion_oracle.report_repo import (
    InMemoryCompletionOracleReportRepository,
)
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportArchiveRepository,
    CompletionOracleReportFilterSpec,
)
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit

_REVIEWER = str(as_uuid("completion-reviewer"))
_EXECUTOR = "executor-1"


def _reviewer_identity(label: str = "completion-reviewer") -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(label),
        name="Ada",
        role=COMPLETION_REVIEWER_ROLE_NAME,
        department="Quality Assurance",
        model=ModelConfig(
            provider="example-provider",
            model_id="example-capable-001",
            capability="capable",
        ),
        hiring_date=date(2026, 1, 15),
    )


def _staffing(*holders: AgentIdentity) -> RoleStaffingService:
    registry = mock_of[AgentRegistryService](
        list_by_role=AsyncMock(
            spec=AgentRegistryService.list_by_role, return_value=holders
        )
    )
    return RoleStaffingService(registry=registry)


def _input(*, executor: str = _EXECUTOR) -> CompletionOracleReviewInput:
    return CompletionOracleReviewInput(
        task_id="task-1",
        execution_id="exec-1",
        deliverable_content="the deliverable",
        acceptance_criteria=("criterion one",),
        executor_agent_id=executor,
    )


def _report(verdict: CompletionOracleVerdict) -> CompletionOracleReport:
    return CompletionOracleReport(
        execution_id="exec-1",
        task_id="task-1",
        reviewer_agent_id=_REVIEWER,
        executor_agent_id=_EXECUTOR,
        verdict=verdict,
        summary="review complete",
    )


class _ScriptedRunner:
    """Files a preset report (or nothing / raises) into the gate's repo."""

    def __init__(
        self,
        repo: InMemoryCompletionOracleReportRepository,
        *,
        report: CompletionOracleReport | None = None,
        raise_dispatch: bool = False,
    ) -> None:
        self._repo = repo
        self._report = report
        self._raise = raise_dispatch
        self.calls = 0
        self.reviewers: list[str] = []

    async def run(
        self,
        *,
        review_input: CompletionOracleReviewInput,
        reviewer: AgentIdentity,
    ) -> None:
        self.calls += 1
        self.reviewers.append(str(reviewer.id))
        if self._raise:
            msg = "dispatch failed"
            raise CompletionOracleDispatchError(msg)
        if self._report is not None:
            await self._repo.put(
                execution_id=review_input.execution_id, report=self._report
            )


class _RaisingArchive:
    """Durable archive that always fails its append (fail-open probe).

    Implements :class:`CompletionOracleReportArchiveRepository` so the gate
    helper types it to the protocol rather than ``object`` + a type-ignore.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def append(self, record: CompletionOracleReportRecord, /) -> None:
        self.calls += 1
        msg = "archive backend down"
        raise QueryError(msg)

    async def query(
        self,
        filter_spec: CompletionOracleReportFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CompletionOracleReportRecord, ...]:
        return ()

    async def count(self, filter_spec: CompletionOracleReportFilterSpec, /) -> int:
        return 0

    async def purge_before(self, threshold: datetime, /) -> int:
        return 0


class _NowRaisingClock(FakeClock):
    """Clock whose wall-clock read fails, probing the archive fail-open boundary.

    ``monotonic`` still works (the gate times the evaluation with it), but
    ``now`` -- called only when constructing the archive record -- raises, so a
    timestamp fault must be swallowed like an append failure.
    """

    @override
    def now(self) -> datetime:
        msg = "clock backend unavailable"
        raise RuntimeError(msg)


def _gate(
    runner: _ScriptedRunner,
    repo: InMemoryCompletionOracleReportRepository,
    *,
    report_archive: CompletionOracleReportArchiveRepository | None = None,
    staffing: RoleStaffingService | None = None,
) -> CompletionOracleGateService:
    return CompletionOracleGateService(
        agent_runner=runner,
        report_repo=repo,
        staffing=staffing if staffing is not None else _staffing(_reviewer_identity()),
        report_archive=report_archive,
        clock=FakeClock(),
    )


class TestCompletionOracleGate:
    @pytest.mark.parametrize(
        "verdict",
        [CompletionOracleVerdict.APPROVE, CompletionOracleVerdict.REJECT],
    )
    async def test_verdict_returned_and_reviewer_dispatched_once(
        self, verdict: CompletionOracleVerdict
    ) -> None:
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(verdict))
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is verdict
        # The reviewer agent was genuinely dispatched exactly once (a lazier
        # test would pass even if the report appeared without a reviewer run).
        assert runner.calls == 1

    async def test_stale_verdict_mismatch_escalates(self) -> None:
        # A report left under the queried key from another run (its embedded
        # execution_id differs) must be discarded, not passed through.
        repo = InMemoryCompletionOracleReportRepository()
        stale = CompletionOracleReport(
            execution_id="other-exec",
            task_id="other-task",
            reviewer_agent_id=_REVIEWER,
            executor_agent_id=_EXECUTOR,
            verdict=CompletionOracleVerdict.APPROVE,
            summary="stale report from another run",
        )
        runner = _ScriptedRunner(repo, report=stale)
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.ESCALATE

    async def test_forged_reviewer_or_executor_id_escalates(self) -> None:
        # A filed report whose reviewer / executor ids differ from the trusted
        # context (its execution/task ids match) must be discarded: forged
        # identities could otherwise satisfy the self-review guard while the
        # real executor reviewed its own work.
        repo = InMemoryCompletionOracleReportRepository()
        forged = CompletionOracleReport(
            execution_id="exec-1",
            task_id="task-1",
            reviewer_agent_id="impostor-reviewer",
            executor_agent_id="impostor-executor",
            verdict=CompletionOracleVerdict.APPROVE,
            summary="forged identities",
        )
        runner = _ScriptedRunner(repo, report=forged)
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.ESCALATE

    async def test_archive_failure_does_not_alter_verdict(self) -> None:
        # The durable archive is fail-OPEN: a write failure is swallowed and the
        # decided verdict still stands (the one fail-open path in the gate).
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.REJECT))
        archive = _RaisingArchive()
        result = await _gate(runner, repo, report_archive=archive).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.REJECT
        assert archive.calls == 1

    async def test_archive_record_construction_failure_does_not_alter_verdict(
        self,
    ) -> None:
        # The record construction + clock read sit inside the fail-OPEN archive
        # boundary: a clock fault must be swallowed, leaving the decided verdict
        # intact, and the append is never reached.
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.APPROVE))
        archive = _RaisingArchive()
        gate = CompletionOracleGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffing(_reviewer_identity()),
            report_archive=archive,
            clock=_NowRaisingClock(),
        )
        result = await gate.evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.APPROVE
        assert archive.calls == 0

    async def test_missing_verdict_escalates(self) -> None:
        # Runner returns without filing a verdict: fail-CLOSED to ESCALATE.
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=None)
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.ESCALATE

    async def test_dispatch_failure_escalates(self) -> None:
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, raise_dispatch=True)
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.ESCALATE

    async def test_self_review_escalates_without_dispatch(self) -> None:
        # The only holder IS the executor: selection excludes it, so no
        # independent reviewer remains. Escalate, never dispatch.
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.APPROVE))
        result = await _gate(runner, repo).evaluate(_input(executor=_REVIEWER))
        assert result.verdict is CompletionOracleVerdict.ESCALATE
        assert runner.calls == 0


class TestReviewerSelection:
    """The reviewer is chosen per review from the roster, never carried."""

    async def test_the_selected_holder_is_the_one_dispatched(self) -> None:
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.APPROVE))
        reviewer = _reviewer_identity()

        await _gate(runner, repo, staffing=_staffing(reviewer)).evaluate(_input())

        assert runner.reviewers == [str(reviewer.id)]

    async def test_no_holder_escalates_without_dispatch(self) -> None:
        # Nobody holds the role: fail CLOSED naming that condition rather than
        # letting the deliverable through unreviewed.
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.APPROVE))

        result = await _gate(runner, repo, staffing=_staffing()).evaluate(_input())

        assert result.verdict is CompletionOracleVerdict.ESCALATE
        assert runner.calls == 0
        assert "role" in result.report.summary
        # The caller routes the park on this, not on the summary text.
        assert result.reviewer_unstaffed is True

    async def test_an_ordinary_escalation_is_not_flagged_unstaffed(self) -> None:
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, raise_dispatch=True)

        result = await _gate(runner, repo).evaluate(_input())

        assert result.verdict is CompletionOracleVerdict.ESCALATE
        assert result.reviewer_unstaffed is False

    async def test_the_unstaffed_report_is_distinguishable_from_a_fault(self) -> None:
        # An unstaffed org and a reviewer that vanished mid-flight are answered
        # by different people, so their reports must not read the same.
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, raise_dispatch=True)

        unstaffed = await _gate(runner, repo, staffing=_staffing()).evaluate(_input())
        faulted = await _gate(runner, repo).evaluate(_input())

        assert unstaffed.report.reviewer_agent_id != faulted.report.reviewer_agent_id
        assert unstaffed.report.summary != faulted.report.summary

    async def test_the_verdict_is_validated_against_the_selected_reviewer(self) -> None:
        # A report filed under a DIFFERENT reviewer than the one the gate chose
        # is a forged identity, not a verdict.
        repo = InMemoryCompletionOracleReportRepository()
        other = CompletionOracleReport(
            execution_id="exec-1",
            task_id="task-1",
            reviewer_agent_id=str(as_uuid("someone-else")),
            executor_agent_id=_EXECUTOR,
            verdict=CompletionOracleVerdict.APPROVE,
            summary="filed by an identity the gate never selected",
        )
        runner = _ScriptedRunner(repo, report=other)

        result = await _gate(runner, repo).evaluate(_input())

        assert result.verdict is CompletionOracleVerdict.ESCALATE


class TestSelfReviewInvariant:
    def test_report_rejects_reviewer_equals_executor(self) -> None:
        with pytest.raises(ValueError, match="must differ from"):
            CompletionOracleReport(
                execution_id="e",
                task_id="t",
                reviewer_agent_id="same",
                executor_agent_id="same",
                verdict=CompletionOracleVerdict.APPROVE,
                summary="s",
            )
