"""Unit tests for the Layer 2 peer-review gate (fail-CLOSED orchestration)."""

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.engine.completion_oracle.errors import CompletionOracleDispatchError
from synthorg.engine.completion_oracle.gate import CompletionOracleGateService
from synthorg.engine.completion_oracle.report_repo import (
    InMemoryCompletionOracleReportRepository,
)
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReport,
    CompletionOracleVerdict,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_REVIEWER = "completion-reviewer"
_EXECUTOR = "executor-1"


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

    async def run(self, *, review_input: CompletionOracleReviewInput) -> None:
        self.calls += 1
        if self._raise:
            msg = "dispatch failed"
            raise CompletionOracleDispatchError(msg)
        if self._report is not None:
            await self._repo.put(
                execution_id=review_input.execution_id, report=self._report
            )


class _RaisingArchive:
    """Durable archive that always fails its append (fail-open probe)."""

    def __init__(self) -> None:
        self.calls = 0

    async def append(self, record: object, /) -> None:
        self.calls += 1
        msg = "archive backend down"
        raise QueryError(msg)

    async def query(
        self, filter_spec: object, *, limit: int = 100, offset: int = 0
    ) -> tuple[object, ...]:
        return ()

    async def purge_before(self, threshold: object, /) -> int:
        return 0


def _gate(
    runner: _ScriptedRunner,
    repo: InMemoryCompletionOracleReportRepository,
    *,
    report_archive: object | None = None,
) -> CompletionOracleGateService:
    return CompletionOracleGateService(
        agent_runner=runner,
        report_repo=repo,
        reviewer_agent_id=_REVIEWER,
        report_archive=report_archive,  # type: ignore[arg-type]
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

    async def test_archive_failure_does_not_alter_verdict(self) -> None:
        # The durable archive is fail-OPEN: a write failure is swallowed and the
        # decided verdict still stands (the one fail-open path in the gate).
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.REJECT))
        archive = _RaisingArchive()
        result = await _gate(runner, repo, report_archive=archive).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.REJECT
        assert archive.calls == 1

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
        # Executor == reviewer: no distinct reviewer, escalate, never dispatch.
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.APPROVE))
        result = await _gate(runner, repo).evaluate(_input(executor=_REVIEWER))
        assert result.verdict is CompletionOracleVerdict.ESCALATE
        assert runner.calls == 0


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
