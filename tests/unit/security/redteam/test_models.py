"""Unit tests for adversarial red-team domain models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import AutonomyLevel
from synthorg.security.redteam.models import (
    MAX_FINDINGS_PER_REPORT,
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamGateResult,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamSeverity,
    RedTeamVerdict,
    severity_rank,
)


@pytest.mark.unit
class TestRedTeamAttackSurface:
    """Attack-surface enum members and values."""

    def test_all_members_present(self) -> None:
        assert RedTeamAttackSurface.CORRECTNESS.value == "correctness"
        assert RedTeamAttackSurface.SECURITY.value == "security"
        assert RedTeamAttackSurface.REQUIREMENTS.value == "requirements"
        assert RedTeamAttackSurface.GROUNDING.value == "grounding"

    def test_str_subclass(self) -> None:
        assert isinstance(RedTeamAttackSurface.CORRECTNESS, str)


@pytest.mark.unit
class TestRedTeamSeverity:
    """Severity enum + ordering."""

    def test_all_members_present(self) -> None:
        assert RedTeamSeverity.INFO.value == "info"
        assert RedTeamSeverity.LOW.value == "low"
        assert RedTeamSeverity.MEDIUM.value == "medium"
        assert RedTeamSeverity.HIGH.value == "high"
        assert RedTeamSeverity.CRITICAL.value == "critical"

    def test_severity_rank_strict_total_order(self) -> None:
        ranks = [
            severity_rank(s)
            for s in (
                RedTeamSeverity.INFO,
                RedTeamSeverity.LOW,
                RedTeamSeverity.MEDIUM,
                RedTeamSeverity.HIGH,
                RedTeamSeverity.CRITICAL,
            )
        ]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)


@pytest.mark.unit
class TestRedTeamVerdict:
    """Verdict enum members."""

    def test_all_members_present(self) -> None:
        assert RedTeamVerdict.PASS.value == "pass"
        assert RedTeamVerdict.PASS_WITH_FINDINGS.value == "pass_with_findings"
        assert RedTeamVerdict.BLOCK.value == "block"


@pytest.mark.unit
class TestRedTeamFinding:
    """Finding model: frozen, extra='forbid', evidence-required-on-HIGH."""

    def _ok_kwargs(self) -> dict[str, object]:
        return {
            "attack_surface": RedTeamAttackSurface.SECURITY,
            "severity": RedTeamSeverity.LOW,
            "description": "Missing input length check",
            "evidence": ("L42: read input without length cap",),
        }

    def test_creation_with_minimal_fields(self) -> None:
        finding = RedTeamFinding(
            attack_surface=RedTeamAttackSurface.CORRECTNESS,
            severity=RedTeamSeverity.LOW,
            description="Output format diverges from spec",
        )
        assert finding.evidence == ()
        assert finding.source == "agent"
        assert finding.citations == ()
        assert finding.suggested_fix is None

    def test_frozen(self) -> None:
        finding = RedTeamFinding(**self._ok_kwargs())
        with pytest.raises(ValidationError):
            finding.severity = RedTeamSeverity.HIGH  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            RedTeamFinding(**self._ok_kwargs(), unexpected="x")

    def test_high_severity_requires_evidence(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.SECURITY,
                severity=RedTeamSeverity.HIGH,
                description="Unsanitised SQL concat",
            )
        msg = str(exc_info.value).lower()
        assert "evidence" in msg

    def test_critical_severity_requires_evidence(self) -> None:
        with pytest.raises(ValidationError):
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.SECURITY,
                severity=RedTeamSeverity.CRITICAL,
                description="Hardcoded prod credential leaked",
            )

    def test_medium_severity_evidence_optional(self) -> None:
        finding = RedTeamFinding(
            attack_surface=RedTeamAttackSurface.REQUIREMENTS,
            severity=RedTeamSeverity.MEDIUM,
            description="Brief mentions password reset, deliverable omits it",
        )
        assert finding.evidence == ()

    def test_source_literal_values(self) -> None:
        finding = RedTeamFinding(
            attack_surface=RedTeamAttackSurface.GROUNDING,
            severity=RedTeamSeverity.LOW,
            description="numeric claim without citation",
            source="heuristic",
        )
        assert finding.source == "heuristic"


@pytest.mark.unit
class TestRedTeamReport:
    """Report model: frozen, bounded findings."""

    def _finding(
        self,
        severity: RedTeamSeverity = RedTeamSeverity.LOW,
    ) -> RedTeamFinding:
        return RedTeamFinding(
            attack_surface=RedTeamAttackSurface.CORRECTNESS,
            severity=severity,
            description="example finding",
        )

    def test_creation_minimal(self) -> None:
        report = RedTeamReport(
            execution_id="exec-1",
            task_id="task-1",
            summary="Clean deliverable.",
        )
        assert report.findings == ()

    def test_frozen(self) -> None:
        report = RedTeamReport(
            execution_id="exec-1",
            task_id="task-1",
            summary="x",
        )
        with pytest.raises(ValidationError):
            report.summary = "y"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            RedTeamReport(
                execution_id="exec-1",
                task_id="task-1",
                summary="x",
                unexpected_field="boom",
            )

    def test_findings_bounded(self) -> None:
        too_many = tuple(self._finding() for _ in range(MAX_FINDINGS_PER_REPORT + 1))
        with pytest.raises(ValidationError):
            RedTeamReport(
                execution_id="exec-1",
                task_id="task-1",
                findings=too_many,
                summary="Too many findings",
            )

    def test_findings_at_bound_accepted(self) -> None:
        at_max = tuple(self._finding() for _ in range(MAX_FINDINGS_PER_REPORT))
        report = RedTeamReport(
            execution_id="exec-1",
            task_id="task-1",
            findings=at_max,
            summary="At bound",
        )
        assert len(report.findings) == MAX_FINDINGS_PER_REPORT


@pytest.mark.unit
class TestRedTeamReviewInput:
    """RedTeamReviewInput requires non-empty acceptance_criteria."""

    def test_creation_full(self) -> None:
        review_input = RedTeamReviewInput(
            task_id="task-1",
            execution_id="exec-1",
            deliverable_content="some artifact",
            acceptance_criteria=("crit-1",),
            assigned_agent_id="agent-1",
            autonomy=AutonomyLevel.SEMI,
        )
        assert review_input.autonomy is AutonomyLevel.SEMI

    def test_empty_acceptance_criteria_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RedTeamReviewInput(
                task_id="task-1",
                execution_id="exec-1",
                deliverable_content="x",
                acceptance_criteria=(),
                assigned_agent_id="agent-1",
                autonomy=AutonomyLevel.FULL,
            )

    def test_frozen(self) -> None:
        review_input = RedTeamReviewInput(
            task_id="task-1",
            execution_id="exec-1",
            deliverable_content="x",
            acceptance_criteria=("c",),
            assigned_agent_id="agent-1",
            autonomy=AutonomyLevel.FULL,
        )
        with pytest.raises(ValidationError):
            review_input.autonomy = AutonomyLevel.LOCKED  # type: ignore[misc]


@pytest.mark.unit
class TestRedTeamGateResult:
    """RedTeamGateResult requires non-negative elapsed_seconds."""

    def _report(self) -> RedTeamReport:
        return RedTeamReport(
            execution_id="exec-1",
            task_id="task-1",
            summary="ok",
        )

    def test_creation_minimal(self) -> None:
        result = RedTeamGateResult(
            verdict=RedTeamVerdict.PASS,
            report=self._report(),
            elapsed_seconds=0.0,
        )
        assert result.grounding_claims == ()

    def test_negative_elapsed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RedTeamGateResult(
                verdict=RedTeamVerdict.PASS,
                report=self._report(),
                elapsed_seconds=-0.1,
            )
