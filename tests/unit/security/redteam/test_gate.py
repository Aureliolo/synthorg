"""Unit tests for ``RedTeamGateService``.

The gate's responsibilities split cleanly into four paths:

* HAPPY PASS: agent files a clean report, no grounding flags -> PASS.
* PASS_WITH_FINDINGS: agent files LOW findings, no grounding flags ->
  PASS_WITH_FINDINGS.
* BLOCK: agent files HIGH/CRITICAL findings -> BLOCK regardless of
  autonomy.
* FAIL-OPEN: agent does not file a report -> synthetic INFO finding,
  gate does not raise, completion proceeds.

Tests cover each path plus the grounding-stub merge behaviour and the
observability events.
"""

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.role_catalog import RED_TEAM_ROLE_NAME
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.observability.events.red_team import (
    RED_TEAM_AGENT_FAILED,
    RED_TEAM_AGENT_INVOKED,
    RED_TEAM_GATE_BLOCKED,
    RED_TEAM_GATE_PASSED,
    RED_TEAM_GATE_STARTED,
    RED_TEAM_GROUNDING_CHECK_COMPLETED,
    RED_TEAM_REPORT_MISSING,
    RED_TEAM_REPORT_RECEIVED,
    RED_TEAM_UNSTAFFED,
)
from synthorg.security.redteam.errors import RedTeamDispatchError
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import AgentRunner
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from tests._shared import FakeClock, role_holder, staffing_with


def _red_teamer(label: str = "red-teamer-1") -> AgentIdentity:
    return role_holder(label, role=RED_TEAM_ROLE_NAME)


def _staffed(*holders: AgentIdentity) -> RoleStaffingService:
    """Return staffing answering with *holders*, defaulting to one adversary.

    Args:
        *holders: Explicit roster, or empty for the default single holder.

    Returns:
        The staffing service.
    """
    return staffing_with(*(holders or (_red_teamer(),)))


class _ScriptedRunner:
    """``AgentRunner`` test double that writes a pre-built report."""

    def __init__(
        self,
        *,
        repo: InMemoryRedTeamReportRepository,
        report: RedTeamReport | None,
    ) -> None:
        self._repo = repo
        self._report = report
        self.invocations: int = 0
        self.red_teamers: list[AgentIdentity] = []

    async def run(
        self,
        *,
        review_input: RedTeamReviewInput,
        red_teamer: AgentIdentity,
    ) -> None:
        self.red_teamers.append(red_teamer)
        self.invocations += 1
        if self._report is not None:
            await self._repo.put(
                execution_id=review_input.execution_id,
                report=self._report,
            )


class _RaisingRunner:
    """``AgentRunner`` that raises ``RedTeamDispatchError`` on every call.

    Mirrors the production :class:`AgentEngineRunner` contract: it
    wraps the underlying engine fault in :class:`RedTeamDispatchError`
    with ``__cause__`` set to the original exception. The gate
    distinguishes this from :class:`asyncio.CancelledError` so
    cancellation propagates while infrastructure faults trigger the
    fail-OPEN policy.
    """

    def __init__(self, *, cause: Exception) -> None:
        self._cause = cause
        self.invocations: int = 0
        self.red_teamers: list[AgentIdentity] = []

    async def run(
        self,
        *,
        review_input: RedTeamReviewInput,
        red_teamer: AgentIdentity,
    ) -> None:
        self.red_teamers.append(red_teamer)
        self.invocations += 1
        try:
            raise self._cause  # noqa: TRY301 -- mirrors production runner's wrap-and-raise
        except Exception as exc:
            msg = f"Red-team agent run failed for {review_input.execution_id!r}"
            raise RedTeamDispatchError(msg) from exc


def _clean_input(deliverable: str = "Backend service done.") -> RedTeamReviewInput:
    return RedTeamReviewInput(
        task_id="task-1",
        execution_id="exec-1",
        deliverable_content=deliverable,
        agent_summary="Delivered the change described in the brief.",
        acceptance_criteria=("Login endpoint exposed.",),
        assigned_agent_id="agent-1",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _empty_report() -> RedTeamReport:
    return RedTeamReport(
        execution_id="exec-1",
        task_id="task-1",
        summary="Clean deliverable, no defects identified.",
    )


def _high_finding_report() -> RedTeamReport:
    return RedTeamReport(
        execution_id="exec-1",
        task_id="task-1",
        findings=(
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.REQUIREMENTS,
                severity=RedTeamSeverity.HIGH,
                description="Brief mandates X; deliverable omits it.",
                evidence=("Brief line 4: 'requires X'",),
            ),
        ),
        summary="One HIGH defect: missing required output.",
    )


def _low_finding_report() -> RedTeamReport:
    return RedTeamReport(
        execution_id="exec-1",
        task_id="task-1",
        findings=(
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.CORRECTNESS,
                severity=RedTeamSeverity.LOW,
                description="Inconsistent log format used in one path.",
            ),
        ),
        summary="One LOW finding, no blockers.",
    )


@pytest.fixture
def repo() -> InMemoryRedTeamReportRepository:
    return InMemoryRedTeamReportRepository()


@pytest.fixture
def grounding() -> HeuristicGroundingChecker:
    return HeuristicGroundingChecker()


@pytest.mark.unit
class TestVerdictRouting:
    """Gate verdict reflects the routing matrix."""

    @pytest.mark.asyncio
    async def test_clean_report_passes(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(_clean_input())
        assert result.verdict is RedTeamVerdict.PASS
        assert result.report.findings == ()

    @pytest.mark.asyncio
    async def test_low_finding_passes_with_findings(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_low_finding_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(_clean_input())
        assert result.verdict is RedTeamVerdict.PASS_WITH_FINDINGS

    @pytest.mark.asyncio
    async def test_high_finding_blocks(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_high_finding_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(_clean_input())
        assert result.verdict is RedTeamVerdict.BLOCK


@pytest.mark.unit
class TestGroundingMerge:
    """Heuristic grounding findings merge into report.findings as GROUNDING/LOW."""

    @pytest.mark.asyncio
    async def test_heuristic_grounding_added_to_findings(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(
            _clean_input("Revenue grew 47% last quarter. Login endpoint exposed."),
        )
        grounding_findings = [
            f
            for f in result.report.findings
            if f.attack_surface is RedTeamAttackSurface.GROUNDING
        ]
        assert len(grounding_findings) >= 1
        for f in grounding_findings:
            assert f.source == "heuristic"
            assert f.severity is RedTeamSeverity.LOW

    @pytest.mark.asyncio
    async def test_heuristic_grounding_does_not_block(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(
            _clean_input("Revenue grew 47% last quarter."),
        )
        # Heuristic finds it but cannot escalate above LOW; verdict is
        # PASS_WITH_FINDINGS, not BLOCK.
        assert result.verdict is RedTeamVerdict.PASS_WITH_FINDINGS
        assert len(result.grounding_claims) >= 1


@pytest.mark.unit
class TestFailOpen:
    """Agent failure paths fall back to a synthetic INFO finding."""

    @pytest.mark.asyncio
    async def test_agent_raises_falls_back_to_info_finding(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _RaisingRunner(cause=RuntimeError("provider down"))
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(_clean_input())
        info_findings = [
            f for f in result.report.findings if f.severity is RedTeamSeverity.INFO
        ]
        assert len(info_findings) == 1
        assert result.verdict is RedTeamVerdict.PASS_WITH_FINDINGS

    @pytest.mark.asyncio
    async def test_agent_returns_without_filing_report(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=None)
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(_clean_input())
        info_findings = [
            f for f in result.report.findings if f.severity is RedTeamSeverity.INFO
        ]
        assert len(info_findings) == 1


@pytest.mark.unit
class TestGroundingErrorPath:
    """Grounding checker exception falls back to empty tuple."""

    @pytest.mark.asyncio
    async def test_grounding_exception_does_not_crash_gate(
        self,
        repo: InMemoryRedTeamReportRepository,
    ) -> None:
        class _FailingChecker:
            async def check(self, **kwargs: object) -> tuple[object, ...]:
                del kwargs
                msg = "grounding subsystem down"
                raise ValueError(msg)

        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=_FailingChecker(),  # type: ignore[arg-type]
            clock=FakeClock(),
        )
        result = await gate.evaluate(_clean_input())
        assert result.grounding_claims == ()
        # Verdict is PASS because the empty report has no agent findings
        # and the grounding stub failed open with no claims.
        assert result.verdict is RedTeamVerdict.PASS


@pytest.mark.unit
class TestObservability:
    """Gate emits the expected event constants in expected order.

    Uses ``structlog.testing.capture_logs`` because :func:`get_logger`
    returns a structlog logger that bypasses the stdlib ``caplog``
    fixture.
    """

    @pytest.mark.asyncio
    async def test_blocked_path_emits_full_event_sequence(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_high_finding_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        with structlog.testing.capture_logs() as cap:
            await gate.evaluate(_clean_input())
        events = [entry["event"] for entry in cap]
        ordered_events = [
            event
            for event in events
            if event
            in (
                RED_TEAM_GATE_STARTED,
                RED_TEAM_AGENT_INVOKED,
                RED_TEAM_REPORT_RECEIVED,
                RED_TEAM_GROUNDING_CHECK_COMPLETED,
                RED_TEAM_GATE_BLOCKED,
            )
        ]
        assert ordered_events == [
            RED_TEAM_GATE_STARTED,
            RED_TEAM_AGENT_INVOKED,
            RED_TEAM_REPORT_RECEIVED,
            RED_TEAM_GROUNDING_CHECK_COMPLETED,
            RED_TEAM_GATE_BLOCKED,
        ]

    @pytest.mark.asyncio
    async def test_clean_path_emits_passed_event(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        with structlog.testing.capture_logs() as cap:
            await gate.evaluate(_clean_input())
        events = [entry["event"] for entry in cap]
        assert RED_TEAM_GATE_PASSED in events

    @pytest.mark.asyncio
    async def test_agent_failure_emits_failed_event(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _RaisingRunner(cause=RuntimeError("boom"))
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        with structlog.testing.capture_logs() as cap:
            await gate.evaluate(_clean_input())
        events = [entry["event"] for entry in cap]
        assert RED_TEAM_AGENT_FAILED in events

    @pytest.mark.asyncio
    async def test_missing_report_emits_report_missing_event(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        runner: AgentRunner = _ScriptedRunner(repo=repo, report=None)
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        with structlog.testing.capture_logs() as cap:
            await gate.evaluate(_clean_input())
        events = [entry["event"] for entry in cap]
        assert RED_TEAM_REPORT_MISSING in events


@pytest.mark.unit
class TestAdversarySelection:
    """Who attacks the deliverable is decided per evaluation."""

    @pytest.mark.asyncio
    async def test_the_selected_holder_is_who_runs(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        holder = _red_teamer("attacker-7")
        runner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(holder),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        await gate.evaluate(_clean_input())
        assert [a.id for a in runner.red_teamers] == [holder.id]

    @pytest.mark.asyncio
    async def test_an_unstaffed_role_blocks_rather_than_failing_open(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        """The one condition this fail-OPEN gate refuses to pass.

        A verifier defect must not hold up the org; nobody holding the role
        is a configuration state, and passing work an enabled adversarial
        gate never saw would make its guarantee depend on staffing luck.
        """
        runner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=staffing_with(),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        with structlog.testing.capture_logs() as cap:
            result = await gate.evaluate(_clean_input())
        assert result.verdict is RedTeamVerdict.BLOCK
        assert result.red_team_unstaffed is True
        assert RED_TEAM_ROLE_NAME in result.report.summary
        assert runner.invocations == 0
        assert RED_TEAM_UNSTAFFED in [entry["event"] for entry in cap]

    @pytest.mark.asyncio
    async def test_the_executor_is_never_its_own_adversary(
        self,
        repo: InMemoryRedTeamReportRepository,
        grounding: HeuristicGroundingChecker,
    ) -> None:
        executor_holds_it = _red_teamer("agent-1")
        runner = _ScriptedRunner(repo=repo, report=_empty_report())
        gate = RedTeamGateService(
            agent_runner=runner,
            report_repo=repo,
            staffing=_staffed(executor_holds_it),
            grounding_checker=grounding,
            clock=FakeClock(),
        )
        result = await gate.evaluate(
            _clean_input().model_copy(
                update={"assigned_agent_id": str(executor_holds_it.id)}
            )
        )
        assert result.red_team_unstaffed is True
        assert runner.invocations == 0
