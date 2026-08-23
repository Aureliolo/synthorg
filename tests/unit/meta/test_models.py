"""Unit tests for meta-loop domain models."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from synthorg.meta.models import (
    ApplyResult,
    ArchitectureChange,
    CIValidationResult,
    CodeChange,
    CodeOperation,
    ConfigChange,
    ErrorCategorySummary,
    EvolutionMode,
    EvolutionOutcomeSummary,
    GuardResult,
    GuardVerdict,
    ImprovementProposal,
    MetricSummary,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    PromptChange,
    ProposalAltitude,
    ProposalRationale,
    ProposalStatus,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
    RollbackOperation,
    RollbackPlan,
    RolloutOutcome,
    RolloutResult,
    RolloutStrategyType,
    RuleMatch,
    RuleSeverity,
    TrendDirection,
)

pytestmark = pytest.mark.unit

# ── Helpers ────────────────────────────────────────────────────────


def _make_rationale() -> ProposalRationale:
    return ProposalRationale(
        signal_summary="Quality declining across engineering",
        pattern_detected="3 consecutive 7d windows of decline",
        expected_impact="Stabilize quality by reducing parallel tasks",
        confidence_reasoning="Strong signal, consistent pattern",
    )


def _make_rollback_plan() -> RollbackPlan:
    return RollbackPlan(
        operations=(
            RollbackOperation(
                operation_type="revert_config",
                target="task_engine.max_parallel_tasks",
                previous_value=5,
                description="Revert max parallel tasks to 5",
            ),
        ),
        validation_check="max_parallel_tasks equals 5 after rollback",
    )


def _make_config_proposal(**kwargs: object) -> ImprovementProposal:
    defaults: dict[str, object] = {
        "altitude": ProposalAltitude.CONFIG_TUNING,
        "title": "Reduce parallel tasks",
        "description": "Lower max parallel tasks to reduce overhead",
        "rationale": _make_rationale(),
        "config_changes": (
            ConfigChange(
                path="task_engine.max_parallel_tasks",
                old_value=5,
                new_value=3,
                description="Reduce to lower coordination overhead",
            ),
        ),
        "rollback_plan": _make_rollback_plan(),
        "confidence": 0.85,
    }
    defaults.update(kwargs)
    return ImprovementProposal(**defaults)  # type: ignore[arg-type]


def _make_performance_summary(**kwargs: object) -> OrgPerformanceSummary:
    defaults: dict[str, object] = {
        "avg_quality_score": 7.5,
        "avg_success_rate": 0.85,
        "avg_collaboration_score": 6.0,
        "agent_count": 10,
    }
    defaults.update(kwargs)
    return OrgPerformanceSummary(**defaults)  # type: ignore[arg-type]


def _make_budget_summary(**kwargs: object) -> OrgBudgetSummary:
    defaults: dict[str, object] = {
        "total_spend": 150.0,
        "productive_ratio": 0.6,
        "coordination_ratio": 0.3,
        "system_ratio": 0.1,
        "forecast_confidence": 0.8,
        "orchestration_overhead": 0.5,
    }
    defaults.update(kwargs)
    return OrgBudgetSummary(**defaults)  # type: ignore[arg-type]


def _make_snapshot(**kwargs: object) -> OrgSignalSnapshot:
    defaults: dict[str, object] = {
        "performance": _make_performance_summary(),
        "budget": _make_budget_summary(),
        "coordination": OrgCoordinationSummary(),
        "errors": OrgErrorSummary(),
        "evolution": OrgEvolutionSummary(),
        "telemetry": OrgTelemetrySummary(),
    }
    defaults.update(kwargs)
    return OrgSignalSnapshot(**defaults)  # type: ignore[arg-type]


# ── Enums ──────────────────────────────────────────────────────────


class TestEnums:
    """Enum value tests."""

    def test_proposal_altitude_values(self) -> None:
        assert ProposalAltitude.CONFIG_TUNING.value == "config_tuning"
        assert ProposalAltitude.ARCHITECTURE.value == "architecture"
        assert ProposalAltitude.PROMPT_TUNING.value == "prompt_tuning"
        assert ProposalAltitude.CODE_MODIFICATION.value == "code_modification"

    def test_code_operation_values(self) -> None:
        assert CodeOperation.CREATE.value == "create"
        assert CodeOperation.MODIFY.value == "modify"
        assert CodeOperation.DELETE.value == "delete"

    def test_proposal_status_values(self) -> None:
        assert ProposalStatus.PENDING.value == "pending"
        assert ProposalStatus.APPLIED.value == "applied"
        assert ProposalStatus.REGRESSED.value == "regressed"

    def test_rollout_strategy_type_values(self) -> None:
        assert RolloutStrategyType.BEFORE_AFTER.value == "before_after"
        assert RolloutStrategyType.CANARY.value == "canary"

    def test_evolution_mode_values(self) -> None:
        assert EvolutionMode.ORG_WIDE.value == "org_wide"
        assert EvolutionMode.OVERRIDE.value == "override"
        assert EvolutionMode.ADVISORY.value == "advisory"

    def test_rule_severity_values(self) -> None:
        assert RuleSeverity.INFO.value == "info"
        assert RuleSeverity.WARNING.value == "warning"
        assert RuleSeverity.CRITICAL.value == "critical"

    def test_trend_direction_values(self) -> None:
        assert TrendDirection.IMPROVING.value == "improving"
        assert TrendDirection.DECLINING.value == "declining"
        assert TrendDirection.STABLE.value == "stable"


# ── RollbackPlan ───────────────────────────────────────────────────


class TestRollbackPlan:
    """RollbackPlan model tests."""

    def test_valid_plan(self) -> None:
        plan = _make_rollback_plan()
        assert len(plan.operations) == 1
        assert plan.dependencies == ()
        assert plan.validation_check

    def test_empty_operations_rejected(self) -> None:
        with pytest.raises(ValidationError, match="too_short"):
            RollbackPlan(
                operations=(),
                validation_check="check something",
            )

    def test_frozen(self) -> None:
        plan = _make_rollback_plan()
        with pytest.raises(ValidationError):
            plan.validation_check = "new check"  # type: ignore[misc]


# ── ImprovementProposal ───────────────────────────────────────────


class TestImprovementProposal:
    """ImprovementProposal model tests."""

    def test_valid_config_proposal(self) -> None:
        proposal = _make_config_proposal()
        assert proposal.altitude == ProposalAltitude.CONFIG_TUNING
        assert proposal.status == ProposalStatus.PENDING
        assert proposal.change_count == 1
        assert isinstance(proposal.id, UUID)

    def test_config_proposal_without_changes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="config_change"):
            _make_config_proposal(config_changes=())

    def test_architecture_proposal_without_changes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="architecture_change"):
            _make_config_proposal(
                altitude=ProposalAltitude.ARCHITECTURE,
                config_changes=(),
                architecture_changes=(),
            )

    def test_prompt_proposal_without_changes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="prompt_change"):
            _make_config_proposal(
                altitude=ProposalAltitude.PROMPT_TUNING,
                config_changes=(),
                prompt_changes=(),
            )

    def test_architecture_proposal_valid(self) -> None:
        proposal = _make_config_proposal(
            altitude=ProposalAltitude.ARCHITECTURE,
            config_changes=(),
            architecture_changes=(
                ArchitectureChange(
                    operation="create_role",
                    target_name="security_auditor",
                    description="Add a security auditor role",
                ),
            ),
        )
        assert proposal.altitude == ProposalAltitude.ARCHITECTURE
        assert proposal.change_count == 1

    def test_prompt_proposal_valid(self) -> None:
        proposal = _make_config_proposal(
            altitude=ProposalAltitude.PROMPT_TUNING,
            config_changes=(),
            prompt_changes=(
                PromptChange(
                    principle_text="Always consider security implications",
                    target_scope="all",
                    description="Org-wide security awareness",
                ),
            ),
        )
        assert proposal.altitude == ProposalAltitude.PROMPT_TUNING
        assert proposal.prompt_changes[0].evolution_mode == EvolutionMode.ORG_WIDE

    def test_confidence_bounds(self) -> None:
        _make_config_proposal(confidence=0.0)
        _make_config_proposal(confidence=1.0)
        with pytest.raises(ValidationError):
            _make_config_proposal(confidence=-0.1)
        with pytest.raises(ValidationError):
            _make_config_proposal(confidence=1.1)

    def test_change_count_computed(self) -> None:
        proposal = _make_config_proposal(
            altitude=ProposalAltitude.CONFIG_TUNING,
            config_changes=(
                ConfigChange(path="a.b", old_value=1, new_value=2, description="d"),
                ConfigChange(path="c.d", old_value=3, new_value=4, description="d"),
            ),
        )
        assert proposal.change_count == 2

    def test_frozen(self) -> None:
        proposal = _make_config_proposal()
        with pytest.raises(ValidationError):
            proposal.status = ProposalStatus.APPROVED  # type: ignore[misc]

    def test_observation_window_minimum(self) -> None:
        _make_config_proposal(observation_window_hours=1)
        with pytest.raises(ValidationError):
            _make_config_proposal(observation_window_hours=0)

    def test_default_id_is_unique(self) -> None:
        p1 = _make_config_proposal()
        p2 = _make_config_proposal()
        assert p1.id != p2.id


# ── RuleMatch ──────────────────────────────────────────────────────


class TestRuleMatch:
    """RuleMatch model tests."""

    def test_valid_match(self) -> None:
        match = RuleMatch(
            rule_name="quality_declining",
            severity=RuleSeverity.WARNING,
            description="Quality declining in engineering",
            suggested_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        )
        assert match.rule_name == "quality_declining"
        assert match.severity == RuleSeverity.WARNING

    def test_empty_altitudes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="too_short"):
            RuleMatch(
                rule_name="test",
                severity=RuleSeverity.INFO,
                description="test",
                suggested_altitudes=(),
            )

    def test_signal_context_default_empty(self) -> None:
        match = RuleMatch(
            rule_name="test",
            severity=RuleSeverity.INFO,
            description="test",
            suggested_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        )
        assert match.signal_context == {}


# ── GuardResult ────────────────────────────────────────────────────


class TestGuardResult:
    """GuardResult model tests."""

    def test_passed_without_reason(self) -> None:
        result = GuardResult(
            guard_name="scope_check",
            verdict=GuardVerdict.PASSED,
        )
        assert result.verdict == GuardVerdict.PASSED
        assert result.reason is None

    def test_rejected_requires_reason(self) -> None:
        with pytest.raises(ValidationError, match="reason"):
            GuardResult(
                guard_name="scope_check",
                verdict=GuardVerdict.REJECTED,
            )

    def test_rejected_with_reason(self) -> None:
        result = GuardResult(
            guard_name="scope_check",
            verdict=GuardVerdict.REJECTED,
            reason="Architecture altitude not enabled",
        )
        assert result.reason == "Architecture altitude not enabled"


# ── RolloutResult ──────────────────────────────────────────────────


class TestRolloutResult:
    """RolloutResult model tests."""

    def test_success_result(self) -> None:
        result = RolloutResult(
            proposal_id=uuid4(),
            outcome=RolloutOutcome.SUCCESS,
            observation_hours_elapsed=48.0,
        )
        assert result.outcome == RolloutOutcome.SUCCESS

    def test_regressed_result_with_verdict(self) -> None:
        result = RolloutResult(
            proposal_id=uuid4(),
            outcome=RolloutOutcome.REGRESSED,
            regression_verdict=RegressionVerdict.THRESHOLD_BREACH,
            observation_hours_elapsed=12.5,
            details="Quality dropped 25%",
        )
        assert result.regression_verdict == RegressionVerdict.THRESHOLD_BREACH


# ── ApplyResult ────────────────────────────────────────────────────


class TestApplyResult:
    """ApplyResult model tests."""

    def test_success(self) -> None:
        result = ApplyResult(success=True, changes_applied=3)
        assert result.success
        assert result.changes_applied == 3

    def test_failure_requires_message(self) -> None:
        with pytest.raises(ValidationError, match="error_message"):
            ApplyResult(success=False, changes_applied=0)

    def test_failure_with_message(self) -> None:
        result = ApplyResult(
            success=False,
            error_message="Config validation failed",
            changes_applied=0,
        )
        assert not result.success


# ── RegressionThresholds ──────────────────────────────────────────


class TestRegressionThresholds:
    """RegressionThresholds model tests."""

    def test_defaults(self) -> None:
        t = RegressionThresholds()
        assert t.quality_drop == 0.10
        assert t.cost_increase == 0.20
        assert t.error_rate_increase == 0.15
        assert t.success_rate_drop == 0.10

    def test_bounds(self) -> None:
        RegressionThresholds(quality_drop=0.0)
        RegressionThresholds(quality_drop=1.0)
        with pytest.raises(ValidationError):
            RegressionThresholds(quality_drop=-0.1)
        with pytest.raises(ValidationError):
            RegressionThresholds(quality_drop=1.1)


# ── RegressionResult ──────────────────────────────────────────────


class TestRegressionResult:
    """RegressionResult model tests."""

    def test_no_regression(self) -> None:
        result = RegressionResult(verdict=RegressionVerdict.NO_REGRESSION)
        assert result.breached_metric is None

    def test_threshold_breach_requires_details(self) -> None:
        with pytest.raises(ValidationError, match="breached metric"):
            RegressionResult(verdict=RegressionVerdict.THRESHOLD_BREACH)

    def test_threshold_breach_requires_values(self) -> None:
        with pytest.raises(ValidationError, match="baseline and current"):
            RegressionResult(
                verdict=RegressionVerdict.THRESHOLD_BREACH,
                breached_metric="quality",
            )

    def test_valid_threshold_breach(self) -> None:
        result = RegressionResult(
            verdict=RegressionVerdict.THRESHOLD_BREACH,
            breached_metric="quality",
            baseline_value=0.85,
            current_value=0.70,
            threshold=0.10,
        )
        assert result.breached_metric == "quality"

    def test_statistical_regression(self) -> None:
        result = RegressionResult(
            verdict=RegressionVerdict.STATISTICAL_REGRESSION,
            breached_metric="success_rate",
            baseline_value=0.90,
            current_value=0.82,
            p_value=0.03,
        )
        assert result.p_value == 0.03


# ── Signal summary models ─────────────────────────────────────────


class TestMetricSummary:
    """MetricSummary model tests."""

    def test_basic(self) -> None:
        m = MetricSummary(name="quality", value=7.5)
        assert m.trend == TrendDirection.STABLE
        assert m.window_days == 7

    def test_with_trend(self) -> None:
        m = MetricSummary(
            name="cost",
            value=150.0,
            trend=TrendDirection.DECLINING,
            window_days=30,
        )
        assert m.trend == TrendDirection.DECLINING


class TestOrgPerformanceSummary:
    """OrgPerformanceSummary model tests."""

    def test_valid(self) -> None:
        s = _make_performance_summary()
        assert s.avg_quality_score == 7.5
        assert s.agent_count == 10

    def test_quality_bounds(self) -> None:
        _make_performance_summary(avg_quality_score=0.0)
        _make_performance_summary(avg_quality_score=10.0)
        with pytest.raises(ValidationError):
            _make_performance_summary(avg_quality_score=-0.1)
        with pytest.raises(ValidationError):
            _make_performance_summary(avg_quality_score=10.1)


class TestOrgBudgetSummary:
    """OrgBudgetSummary model tests."""

    def test_valid(self) -> None:
        s = _make_budget_summary()
        assert s.total_spend == 150.0
        assert s.days_until_exhausted is None

    def test_with_exhaustion_forecast(self) -> None:
        s = _make_budget_summary(days_until_exhausted=14)
        assert s.days_until_exhausted == 14


class TestOrgCoordinationSummary:
    """OrgCoordinationSummary model tests."""

    def test_defaults_all_none(self) -> None:
        s = OrgCoordinationSummary()
        assert s.coordination_efficiency is None
        assert s.sample_count == 0

    def test_with_metrics(self) -> None:
        s = OrgCoordinationSummary(
            coordination_efficiency=0.85,
            coordination_overhead_pct=25.0,
            sample_count=50,
        )
        assert s.coordination_efficiency == 0.85


class TestOrgSignalSnapshot:
    """OrgSignalSnapshot model tests."""

    def test_valid_snapshot(self) -> None:
        snap = _make_snapshot()
        assert snap.performance.avg_quality_score == 7.5
        assert snap.budget.total_spend == 150.0
        assert snap.coordination.sample_count == 0

    def test_frozen(self) -> None:
        snap = _make_snapshot()
        with pytest.raises(ValidationError):
            snap.performance = _make_performance_summary()  # type: ignore[misc]

    def test_collected_at_auto_set(self) -> None:
        snap = _make_snapshot()
        assert snap.collected_at is not None
        assert snap.collected_at.tzinfo is not None


class TestErrorCategorySummary:
    """ErrorCategorySummary model tests."""

    def test_valid(self) -> None:
        s = ErrorCategorySummary(
            category="contradiction",
            count=5,
            avg_severity=2.0,
        )
        assert s.category == "contradiction"
        assert s.trend == TrendDirection.STABLE


class TestEvolutionOutcomeSummary:
    """EvolutionOutcomeSummary model tests."""

    def test_valid(self) -> None:
        s = EvolutionOutcomeSummary(
            agent_id="agent-1",
            axis="prompt_template",
            applied=True,
            proposed_at=datetime.now(UTC),
        )
        assert s.applied


# ── ProposalRationale ──────────────────────────────────────────────


class TestProposalRationale:
    """ProposalRationale model tests."""

    def test_valid(self) -> None:
        r = _make_rationale()
        assert r.signal_summary

    def test_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProposalRationale(
                signal_summary="  ",
                pattern_detected="test",
                expected_impact="test",
                confidence_reasoning="test",
            )


# ── Change models ──────────────────────────────────────────────────


class TestConfigChange:
    """ConfigChange model tests."""

    def test_valid(self) -> None:
        c = ConfigChange(
            path="budget.monthly_usd",
            old_value=1000,
            new_value=1200,
            description="Increase budget",
        )
        assert c.path == "budget.monthly_usd"


class TestArchitectureChange:
    """ArchitectureChange model tests."""

    def test_valid(self) -> None:
        c = ArchitectureChange(
            operation="create_role",
            target_name="data_engineer",
            description="Add data engineering role",
        )
        assert c.payload == {}


class TestPromptChange:
    """PromptChange model tests."""

    def test_default_mode(self) -> None:
        c = PromptChange(
            principle_text="Consider security in all decisions",
            target_scope="all",
            description="Security awareness",
        )
        assert c.evolution_mode == EvolutionMode.ORG_WIDE

    def test_override_mode(self) -> None:
        c = PromptChange(
            principle_text="Focus on test coverage",
            target_scope="engineering",
            evolution_mode=EvolutionMode.OVERRIDE,
            description="Testing focus for engineers",
        )
        assert c.evolution_mode == EvolutionMode.OVERRIDE


class TestCodeChange:
    """CodeChange model tests."""

    def test_valid_create(self) -> None:
        c = CodeChange(
            file_path="src/synthorg/meta/strategies/new_algo.py",
            operation=CodeOperation.CREATE,
            new_content="class NewAlgo:\n    pass\n",
            description="Add new algorithm",
            reasoning="Quality declining needs better approach",
        )
        assert c.operation == CodeOperation.CREATE
        assert c.old_content == ""

    def test_valid_modify(self) -> None:
        c = CodeChange(
            file_path="src/synthorg/meta/guards/scope_check.py",
            operation=CodeOperation.MODIFY,
            old_content="original content",
            new_content="modified content",
            description="Improve scope check",
            reasoning="Reduce false positives",
        )
        assert c.operation == CodeOperation.MODIFY

    def test_valid_delete(self) -> None:
        c = CodeChange(
            file_path="src/synthorg/meta/strategies/deprecated.py",
            operation=CodeOperation.DELETE,
            old_content="old code to preserve for rollback",
            description="Remove deprecated strategy",
            reasoning="No longer triggered by any rule",
        )
        assert c.new_content == ""

    def test_create_with_old_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"create.*empty old_content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.CREATE,
                old_content="should not be here",
                new_content="new stuff",
                description="d",
                reasoning="r",
            )

    def test_create_without_new_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"create.*non-empty new_content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.CREATE,
                description="d",
                reasoning="r",
            )

    def test_modify_without_old_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"modify.*non-empty old_content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.MODIFY,
                new_content="new stuff",
                description="d",
                reasoning="r",
            )

    def test_modify_without_new_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"modify.*non-empty new_content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.MODIFY,
                old_content="old stuff",
                description="d",
                reasoning="r",
            )

    def test_modify_identical_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must change the content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.MODIFY,
                old_content="same",
                new_content="same",
                description="d",
                reasoning="r",
            )

    def test_delete_with_new_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"delete.*empty new_content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.DELETE,
                old_content="old",
                new_content="should not be here",
                description="d",
                reasoning="r",
            )

    def test_delete_without_old_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"delete.*non-empty old_content"):
            CodeChange(
                file_path="src/x.py",
                operation=CodeOperation.DELETE,
                description="d",
                reasoning="r",
            )

    def test_frozen(self) -> None:
        c = CodeChange(
            file_path="src/x.py",
            operation=CodeOperation.CREATE,
            new_content="content",
            description="d",
            reasoning="r",
        )
        with pytest.raises(ValidationError):
            c.file_path = "other"  # type: ignore[misc]


# ── Code modification altitude validation ─────────────────────────


class TestCodeModificationProposal:
    """ImprovementProposal validation for CODE_MODIFICATION altitude."""

    def test_valid_code_modification_proposal(self) -> None:
        proposal = _make_config_proposal(
            altitude=ProposalAltitude.CODE_MODIFICATION,
            config_changes=(),
            code_changes=(
                CodeChange(
                    file_path="src/synthorg/meta/strategies/new.py",
                    operation=CodeOperation.CREATE,
                    new_content="class New:\n    pass\n",
                    description="Add new strategy",
                    reasoning="Quality declining",
                ),
            ),
        )
        assert proposal.altitude == ProposalAltitude.CODE_MODIFICATION
        assert proposal.change_count == 1

    def test_code_modification_without_code_changes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="code_change"):
            _make_config_proposal(
                altitude=ProposalAltitude.CODE_MODIFICATION,
                config_changes=(),
                code_changes=(),
            )

    def test_code_modification_with_config_changes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="code_change"):
            _make_config_proposal(
                altitude=ProposalAltitude.CODE_MODIFICATION,
                code_changes=(
                    CodeChange(
                        file_path="src/x.py",
                        operation=CodeOperation.CREATE,
                        new_content="content",
                        description="d",
                        reasoning="r",
                    ),
                ),
            )

    def test_config_with_code_changes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="config_tuning"):
            _make_config_proposal(
                code_changes=(
                    CodeChange(
                        file_path="src/x.py",
                        operation=CodeOperation.CREATE,
                        new_content="content",
                        description="d",
                        reasoning="r",
                    ),
                ),
            )


# ── CIValidationResult ────────────────────────────────────────────


class TestCIValidationResult:
    """CIValidationResult model tests."""

    def test_all_passed(self) -> None:
        r = CIValidationResult(
            passed=True,
            lint_passed=True,
            typecheck_passed=True,
            tests_passed=True,
            duration_seconds=12.5,
        )
        assert r.passed
        assert r.errors == ()

    def test_lint_failed(self) -> None:
        r = CIValidationResult(
            passed=False,
            lint_passed=False,
            typecheck_passed=True,
            tests_passed=True,
            errors=("ruff: E501 line too long",),
            duration_seconds=3.0,
        )
        assert not r.passed
        assert len(r.errors) == 1

    def test_failure_without_errors_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one error"):
            CIValidationResult(
                passed=False,
                lint_passed=False,
                typecheck_passed=True,
                tests_passed=True,
                duration_seconds=1.0,
            )

    def test_passed_with_failed_subcheck_rejected(self) -> None:
        with pytest.raises(ValidationError, match="conjunction of all sub-checks"):
            CIValidationResult(
                passed=True,
                lint_passed=True,
                typecheck_passed=False,
                tests_passed=True,
                duration_seconds=1.0,
            )

    def test_frozen(self) -> None:
        r = CIValidationResult(
            passed=True,
            lint_passed=True,
            typecheck_passed=True,
            tests_passed=True,
            duration_seconds=1.0,
        )
        with pytest.raises(ValidationError):
            r.passed = False  # type: ignore[misc]
