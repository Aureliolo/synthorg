"""Unit tests for the Layer 2 peer-review gate (fail-CLOSED orchestration)."""

import pytest

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


def _gate(
    runner: _ScriptedRunner, repo: InMemoryCompletionOracleReportRepository
) -> CompletionOracleGateService:
    return CompletionOracleGateService(
        agent_runner=runner,
        report_repo=repo,
        reviewer_agent_id=_REVIEWER,
        clock=FakeClock(),
    )


class TestCompletionOracleGate:
    async def test_approve_verdict_passes(self) -> None:
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.APPROVE))
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.APPROVE

    async def test_reject_verdict_returned(self) -> None:
        repo = InMemoryCompletionOracleReportRepository()
        runner = _ScriptedRunner(repo, report=_report(CompletionOracleVerdict.REJECT))
        result = await _gate(runner, repo).evaluate(_input())
        assert result.verdict is CompletionOracleVerdict.REJECT

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
