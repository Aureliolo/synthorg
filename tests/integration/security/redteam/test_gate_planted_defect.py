"""Planted-defect acceptance test for the red-team gate.

Acceptance contract:

    A deliverable with a planted defect (failing requirement,
    unsupported claim) is caught by the red-team and sent back
    as rework before completion.

The test drives a deliverable with two planted defects (one missing
brief requirement, one ungrounded numeric claim) through the
``RedTeamGateService``. The scripted ``AgentRunner`` emits two HIGH
findings via the ``submit_red_team_report`` tool; the heuristic
grounding checker independently flags the ungrounded claim; the gate
aggregates everything and produces a BLOCK verdict that the
ReviewGateService will route as IN_PROGRESS rework.
"""

import pytest

from synthorg.core.enums import AutonomyLevel
from synthorg.observability.events.red_team import (
    RED_TEAM_GATE_BLOCKED,
    RED_TEAM_GATE_STARTED,
    RED_TEAM_REPORT_RECEIVED,
)
from synthorg.security.redteam import (
    HeuristicGroundingChecker,
    InMemoryRedTeamReportRepository,
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamGateService,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import AgentRunner
from tests._shared import FakeClock


class _ScriptedAgentRunner:
    """Scripted ``AgentRunner`` that writes a pre-built report via the tool.

    Stands in for an LLM-driven ``AgentEngineRunner`` in the acceptance
    path. Instead of invoking ``AgentEngine.run``, it synchronously
    persists the supplied :class:`RedTeamReport` into the repo via the
    ``submit_red_team_report`` tool handler. This keeps the acceptance
    test deterministic (no provider, no model, no token budget) while
    exercising every code path the production gate runs *after* the
    agent has filed its report.
    """

    def __init__(
        self,
        *,
        repo: InMemoryRedTeamReportRepository,
        report: RedTeamReport,
    ) -> None:
        self._repo = repo
        self._report = report
        self.invocations: int = 0

    async def run(
        self,
        *,
        review_input: RedTeamReviewInput,
    ) -> None:
        self.invocations += 1
        await self._repo.put(
            execution_id=review_input.execution_id,
            report=self._report,
        )


_PLANTED_DELIVERABLE: str = (
    "Backend service implementation complete.\n"
    "\n"
    "Authentication endpoint accepts username and password and returns a "
    "session token.\n"
    "Revenue grew 47% last quarter compared to the prior period.\n"
    "Rate limiting is configured at the gateway.\n"
)
"""Deliverable text with two planted defects.

1. Acceptance criterion requires "password reset endpoint"; the
   deliverable describes only the login endpoint.
2. The revenue claim ("grew 47% last quarter") is asserted without
   any source, citation marker, URL, or grounded evidence.
"""

_ACCEPTANCE_CRITERIA: tuple[str, ...] = (
    "Service exposes a login endpoint accepting username and password.",
    "Service exposes a password reset endpoint that emails the user.",
    "All authentication paths are rate-limited at the gateway.",
)


def _build_planted_report(
    *,
    execution_id: str = "exec-planted-defect-001",
    task_id: str = "task-planted-defect-001",
) -> RedTeamReport:
    """The report the red-team agent should produce for the planted defects.

    The ids are parameterised so a caller running the gate against a
    different review_input pair (e.g. ``...002``) can supply matching
    ids and keep the stored report aligned with the review-input keys.
    """
    return RedTeamReport(
        execution_id=execution_id,
        task_id=task_id,
        findings=(
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.REQUIREMENTS,
                severity=RedTeamSeverity.HIGH,
                description=(
                    "Deliverable omits the password-reset endpoint required "
                    "by acceptance criterion 2."
                ),
                evidence=(
                    "Acceptance criterion: 'Service exposes a password reset "
                    "endpoint that emails the user.'",
                    "Deliverable describes only the login endpoint.",
                ),
                suggested_fix=(
                    "Implement POST /auth/password-reset that issues an email "
                    "with a single-use reset link."
                ),
            ),
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.GROUNDING,
                severity=RedTeamSeverity.HIGH,
                description=(
                    "The claim that revenue grew 47% last quarter is asserted "
                    "without a source."
                ),
                evidence=(
                    "Sentence: 'Revenue grew 47% last quarter compared to the "
                    "prior period.'",
                ),
                suggested_fix=(
                    "Cite the originating finance report or remove the claim."
                ),
            ),
        ),
        summary=(
            "Two HIGH-severity defects identified: one missing acceptance "
            "criterion, one ungrounded numeric claim."
        ),
    )


@pytest.mark.integration
async def test_planted_defects_block_completion_via_red_team_gate() -> None:
    """A deliverable with planted defects is BLOCKed by the red-team gate.

    Asserts the full acceptance contract:

    * Gate invokes the agent runner exactly once.
    * The agent files two HIGH findings (REQUIREMENTS + GROUNDING).
    * The heuristic grounding checker independently flags the ungrounded
      numeric claim, producing at least one ``UngroundedClaim``.
    * Verdict is :data:`RedTeamVerdict.BLOCK` (HIGH severity always
      blocks regardless of autonomy).
    * The gate's structured result carries both findings AND the
      grounding-stub claims so downstream rework routing has the full
      structured critique.
    """
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedAgentRunner(
        repo=repo,
        report=_build_planted_report(),
    )
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=HeuristicGroundingChecker(),
        clock=FakeClock(),
    )
    review_input = RedTeamReviewInput(
        task_id="task-planted-defect-001",
        execution_id="exec-planted-defect-001",
        deliverable_content=_PLANTED_DELIVERABLE,
        acceptance_criteria=_ACCEPTANCE_CRITERIA,
        assigned_agent_id="agent-backend-dev-7",
        autonomy=AutonomyLevel.SUPERVISED,
    )

    result = await gate.evaluate(review_input)

    assert result.verdict is RedTeamVerdict.BLOCK
    assert runner.invocations == 1  # type: ignore[attr-defined]

    surfaces = {f.attack_surface for f in result.report.findings}
    assert RedTeamAttackSurface.REQUIREMENTS in surfaces
    assert RedTeamAttackSurface.GROUNDING in surfaces

    severities = {f.severity for f in result.report.findings}
    assert RedTeamSeverity.HIGH in severities

    # The heuristic grounding stub must catch the ungrounded numeric
    # claim on its own; that's what makes the acceptance test honest
    # without depending on a substrate-backed checker.
    assert len(result.grounding_claims) >= 1
    grounded_excerpts = [c.excerpt for c in result.grounding_claims]
    assert any("47%" in excerpt for excerpt in grounded_excerpts)


@pytest.mark.integration
async def test_red_team_emits_observability_events_in_order() -> None:
    """Gate emits the gate-started, report-received, gate-blocked events."""
    import structlog.testing

    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedAgentRunner(
        repo=repo,
        report=_build_planted_report(
            execution_id="exec-planted-defect-002",
            task_id="task-planted-defect-002",
        ),
    )
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=HeuristicGroundingChecker(),
        clock=FakeClock(),
    )

    review_input = RedTeamReviewInput(
        task_id="task-planted-defect-002",
        execution_id="exec-planted-defect-002",
        deliverable_content=_PLANTED_DELIVERABLE,
        acceptance_criteria=_ACCEPTANCE_CRITERIA,
        assigned_agent_id="agent-backend-dev-7",
        autonomy=AutonomyLevel.SUPERVISED,
    )
    with structlog.testing.capture_logs() as cap:
        await gate.evaluate(review_input)

    events_in_order = [
        entry["event"]
        for entry in cap
        if entry["event"]
        in (
            RED_TEAM_GATE_STARTED,
            RED_TEAM_REPORT_RECEIVED,
            RED_TEAM_GATE_BLOCKED,
        )
    ]
    assert events_in_order == [
        RED_TEAM_GATE_STARTED,
        RED_TEAM_REPORT_RECEIVED,
        RED_TEAM_GATE_BLOCKED,
    ]
