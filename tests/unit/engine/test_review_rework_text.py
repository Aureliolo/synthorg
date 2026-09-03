# module-kind: tests
"""The rework hop carries the findings, not the summary alone.

The verdict tool tells a reviewer the findings list is what the assignee
reads and refuses a reject without one, so the hop the assignee reads has
to carry it: a merge briefed with three rounds of findings never saw one.
"""

import pytest

from synthorg.core.task_enums import TaskStatus
from synthorg.engine._review_oracle_gates import _route_non_approving_verdict
from synthorg.engine._review_rework_text import rework_brief
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleFinding,
    CompletionOracleGateResult,
    CompletionOracleReport,
    CompletionOracleVerdict,
)
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamSeverity,
)

pytestmark = pytest.mark.unit


def _finding(
    description: str,
    *,
    evidence: tuple[str, ...] = (),
    suggested_fix: str | None = None,
) -> CompletionOracleFinding:
    # A HIGH or CRITICAL finding must quote evidence; a MEDIUM one need not.
    return CompletionOracleFinding(
        severity=RedTeamSeverity.CRITICAL if evidence else RedTeamSeverity.MEDIUM,
        description=description,
        evidence=evidence,
        suggested_fix=suggested_fix,
    )


class TestReworkBrief:
    def test_no_findings_is_the_lead_and_summary(self) -> None:
        assert rework_brief("Lead", "Summary.", ()) == "Lead: Summary."

    def test_every_finding_becomes_a_numbered_line(self) -> None:
        text = rework_brief(
            "Completion review (reject)",
            "Two defects.",
            (
                _finding(
                    "join.py dispatches to inner_join, which it never defines",
                    evidence=("join.py:41: return inner_join(...)",),
                    suggested_fix="define inner_join beside left_join",
                ),
                _finding("exec.py calls join_rows with two arguments"),
            ),
        )

        assert text.splitlines() == [
            "Completion review (reject): Two defects.",
            (
                "1. [critical] join.py dispatches to inner_join, which it never "
                "defines Evidence: join.py:41: return inner_join(...) Fix: define "
                "inner_join beside left_join"
            ),
            "2. [medium] exec.py calls join_rows with two arguments",
        ]

    def test_a_red_team_finding_renders_the_same_way(self) -> None:
        finding = RedTeamFinding(
            attack_surface=RedTeamAttackSurface.SECURITY,
            severity=RedTeamSeverity.HIGH,
            description="a credential is hardcoded",
            evidence=("config.py:3",),
            suggested_fix="read it from the connection",
        )

        text = rework_brief("Red-team review blocked completion", "S.", (finding,))

        assert (
            "1. [high] a credential is hardcoded Evidence: config.py:3 Fix: read it"
            in text
        )


class TestTheOracleRouterCarriesTheFindings:
    def test_a_reject_sends_the_findings_back(self) -> None:
        report = CompletionOracleReport(
            execution_id="exec-1",
            task_id="task-1",
            executor_agent_id="agent-1",
            verdict=CompletionOracleVerdict.REJECT,
            findings=(
                _finding("inner_join is never defined", evidence=("join.py:41",)),
            ),
            summary="Not mergeable.",
        )
        result = CompletionOracleGateResult(
            verdict=CompletionOracleVerdict.REJECT,
            report=report,
            elapsed_seconds=0.0,
        )

        outcome = _route_non_approving_verdict(
            result, task_id="task-1", execution_id="exec-1"
        )

        assert outcome.target is TaskStatus.IN_PROGRESS
        assert outcome.transition_reason.startswith(
            "Completion review (reject): Not mergeable."
        )
        assert "1. [critical] inner_join is never defined" in outcome.transition_reason
