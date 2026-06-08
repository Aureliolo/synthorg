"""Unit tests for RecoveryResult failure diagnosis fields and infer_failure_category."""

import pytest
from pydantic import ValidationError

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.context import AgentContext
from synthorg.engine.recovery import (
    FailureCategory,
    RecoveryResult,
    infer_failure_category,
    infer_failure_category_without_evidence,
)
from synthorg.engine.stagnation.models import StagnationResult, StagnationVerdict


def _make_recovery_result(  # noqa: PLR0913
    ctx: AgentContext,
    *,
    failure_category: FailureCategory = FailureCategory.TOOL_FAILURE,
    failure_context: dict[str, object] | None = None,
    criteria_failed: tuple[str, ...] = (),
    stagnation_evidence: StagnationResult | None = None,
    checkpoint_context_json: str | None = None,
    resume_attempt: int = 0,
) -> RecoveryResult:
    """Build a RecoveryResult from an AgentContext with FAILED execution."""
    assert ctx.task_execution is not None
    failed = ctx.task_execution.with_transition(
        TaskStatus.FAILED, reason="test failure"
    )
    return RecoveryResult(
        task_execution=failed,
        strategy_type="fail_reassign",
        context_snapshot=ctx.to_snapshot(),
        error_message="test failure",
        failure_category=failure_category,
        failure_context=failure_context if failure_context is not None else {},
        criteria_failed=criteria_failed,
        stagnation_evidence=stagnation_evidence,
        checkpoint_context_json=checkpoint_context_json,
        resume_attempt=resume_attempt,
    )


@pytest.mark.unit
class TestRecoveryResultDiagnosisFields:
    """Tests for the new failure diagnosis fields on RecoveryResult."""

    def test_failure_category_required(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """failure_category is required -- omitting it raises ValidationError."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        assert ctx.task_execution is not None
        failed = ctx.task_execution.with_transition(TaskStatus.FAILED, reason="crash")
        with pytest.raises(ValidationError, match="failure_category"):
            RecoveryResult(  # type: ignore[call-arg]
                task_execution=failed,
                strategy_type="fail_reassign",
                context_snapshot=ctx.to_snapshot(),
                error_message="crash",
            )

    def test_failure_context_defaults_to_empty_dict(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """failure_context has a default_factory -- callers don't pass {}."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        assert ctx.task_execution is not None
        failed = ctx.task_execution.with_transition(TaskStatus.FAILED, reason="crash")
        result = RecoveryResult(
            task_execution=failed,
            strategy_type="fail_reassign",
            context_snapshot=ctx.to_snapshot(),
            error_message="crash",
            failure_category=FailureCategory.TOOL_FAILURE,
        )
        assert result.failure_context == {}

    def test_stagnation_category_requires_evidence(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """failure_category=STAGNATION without stagnation_evidence is rejected."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        with pytest.raises(ValidationError, match="stagnation_evidence is required"):
            _make_recovery_result(
                ctx,
                failure_category=FailureCategory.STAGNATION,
                stagnation_evidence=None,
            )

    def test_stagnation_evidence_forbidden_without_stagnation_category(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """stagnation_evidence set with a non-STAGNATION category is rejected."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        stag = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.9,
        )
        with pytest.raises(ValidationError, match="stagnation_evidence must be None"):
            _make_recovery_result(
                ctx,
                failure_category=FailureCategory.TOOL_FAILURE,
                stagnation_evidence=stag,
            )

    def test_quality_gate_failed_requires_criteria(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """failure_category=QUALITY_GATE_FAILED with empty criteria is rejected."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        with pytest.raises(ValidationError, match="criteria_failed must be non-empty"):
            _make_recovery_result(
                ctx,
                failure_category=FailureCategory.QUALITY_GATE_FAILED,
                criteria_failed=(),
            )

    def test_stagnation_rejects_no_stagnation_verdict(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """STAGNATION category with NO_STAGNATION evidence is self-contradicting.

        The verdict must be a positive stagnation finding (TERMINATE,
        INJECT_PROMPT) to back a STAGNATION recovery result.
        """
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        evidence = StagnationResult(
            verdict=StagnationVerdict.NO_STAGNATION,
            repetition_ratio=0.0,
        )
        with pytest.raises(
            ValidationError,
            match="verdict cannot be NO_STAGNATION",
        ):
            _make_recovery_result(
                ctx,
                failure_category=FailureCategory.STAGNATION,
                stagnation_evidence=evidence,
            )

    def test_criteria_failed_rejects_duplicates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """Duplicate criteria are rejected (set semantics)."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        with pytest.raises(ValidationError, match="Duplicate entries"):
            _make_recovery_result(
                ctx,
                failure_category=FailureCategory.QUALITY_GATE_FAILED,
                criteria_failed=("JWT login", "JWT login"),
            )

    def test_all_fields_populated(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """All diagnosis fields can be populated together."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        stag = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.8,
            cycle_length=3,
        )
        result = _make_recovery_result(
            ctx,
            failure_category=FailureCategory.STAGNATION,
            failure_context={"detector": "tool_repetition"},
            criteria_failed=("Login endpoint returns JWT token",),
            stagnation_evidence=stag,
        )
        assert result.failure_category is FailureCategory.STAGNATION
        assert result.failure_context == {"detector": "tool_repetition"}
        assert result.criteria_failed == ("Login endpoint returns JWT token",)
        assert result.stagnation_evidence is not None
        assert result.stagnation_evidence.repetition_ratio == 0.8

    def test_criteria_failed_defaults_empty(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """criteria_failed defaults to empty tuple."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        result = _make_recovery_result(ctx)
        assert result.criteria_failed == ()

    def test_stagnation_evidence_defaults_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """stagnation_evidence defaults to None."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        result = _make_recovery_result(ctx)
        assert result.stagnation_evidence is None

    def test_failure_context_deep_copied(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """Mutating the original dict does not affect the frozen model."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        nested: dict[str, int] = {"a": 1}
        original: dict[str, object] = {"key": "value", "nested": nested}
        result = _make_recovery_result(ctx, failure_context=original)
        original["key"] = "mutated"
        nested["a"] = 999
        assert result.failure_context["key"] == "value"
        nested_copy = result.failure_context["nested"]
        assert isinstance(nested_copy, dict)
        assert nested_copy["a"] == 1

    def test_frozen(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """Attempting to set fields on a constructed result raises."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        result = _make_recovery_result(ctx)
        with pytest.raises(ValidationError):
            result.failure_category = FailureCategory.TIMEOUT  # type: ignore[misc]


@pytest.mark.unit
class TestInferFailureCategory:
    """Tests for the infer_failure_category helper."""

    @pytest.mark.parametrize(
        ("error_message", "expected"),
        [
            pytest.param(
                "Budget limit exceeded for task",
                FailureCategory.BUDGET_EXCEEDED,
                id="budget",
            ),
            pytest.param(
                "Monthly BUDGET exhausted",
                FailureCategory.BUDGET_EXCEEDED,
                id="budget-upper",
            ),
            pytest.param(
                "Connection timeout to provider",
                FailureCategory.TIMEOUT,
                id="timeout",
            ),
            pytest.param(
                "Request timed out after 30s",
                FailureCategory.TIMEOUT,
                id="timed-out",
            ),
            pytest.param(
                "Stagnation detected: repetitive tool calls",
                FailureCategory.STAGNATION,
                id="stagnation",
            ),
            pytest.param(
                "Delegation failed: no capable agent",
                FailureCategory.DELEGATION_FAILED,
                id="delegation",
            ),
            pytest.param(
                "Quality gate failed: criteria not met",
                FailureCategory.QUALITY_GATE_FAILED,
                id="quality",
            ),
            pytest.param(
                "Acceptance criteria not satisfied",
                FailureCategory.QUALITY_GATE_FAILED,
                id="criteria",
            ),
            pytest.param(
                "Unknown error: something went wrong",
                FailureCategory.UNKNOWN,
                id="unknown-default",
            ),
            pytest.param(
                "NullPointerException in handler",
                FailureCategory.UNKNOWN,
                id="generic-error",
            ),
            pytest.param(
                "",
                FailureCategory.UNKNOWN,
                id="empty-string",
            ),
            pytest.param(
                "Budget timeout stagnation",
                FailureCategory.BUDGET_EXCEEDED,
                id="first-rule-wins-budget",
            ),
            pytest.param(
                "Delegation failed: criteria not met",
                FailureCategory.DELEGATION_FAILED,
                id="first-rule-wins-delegation-over-quality",
            ),
        ],
    )
    def test_keyword_mapping(
        self, error_message: str, expected: FailureCategory
    ) -> None:
        """Each keyword maps to the correct category."""
        assert infer_failure_category(error_message) == expected


@pytest.mark.unit
class TestInferFailureCategoryWithoutEvidence:
    """Tests for ``infer_failure_category_without_evidence``.

    This helper exists so callers that build a ``RecoveryResult``
    without sidecar data (``stagnation_evidence``, ``criteria_failed``)
    can safely classify error messages without triggering the
    cross-field validator.  It should clamp STAGNATION and
    QUALITY_GATE_FAILED -> UNKNOWN while passing other categories
    through unchanged.
    """

    @pytest.mark.parametrize(
        ("error_message", "expected"),
        [
            pytest.param(
                "Stagnation detected: repetitive tool calls",
                FailureCategory.UNKNOWN,
                id="stagnation-clamped-to-unknown",
            ),
            pytest.param(
                "quality gate failed",
                FailureCategory.UNKNOWN,
                id="quality-clamped-to-unknown",
            ),
            pytest.param(
                "Acceptance criteria not satisfied",
                FailureCategory.UNKNOWN,
                id="criteria-clamped-to-unknown",
            ),
            pytest.param(
                "Budget limit exceeded",
                FailureCategory.BUDGET_EXCEEDED,
                id="budget-preserved",
            ),
            pytest.param(
                "Connection timed out",
                FailureCategory.TIMEOUT,
                id="timeout-preserved",
            ),
            pytest.param(
                "Delegation failed: no capable agent",
                FailureCategory.DELEGATION_FAILED,
                id="delegation-preserved",
            ),
            pytest.param(
                "NullPointerException in handler",
                FailureCategory.UNKNOWN,
                id="unknown-passthrough",
            ),
        ],
    )
    def test_clamping_behavior(
        self,
        error_message: str,
        expected: FailureCategory,
    ) -> None:
        """Evidence-required categories clamp to UNKNOWN."""
        assert infer_failure_category_without_evidence(error_message) == expected

    def test_builds_valid_recovery_result_for_stagnation_message(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        """Regression for C1: error messages with 'stagnation' must not crash.

        The original bug: ``FailAndReassignStrategy.recover`` passed
        ``infer_failure_category(error_message)`` directly to
        ``RecoveryResult`` without supplying ``stagnation_evidence``,
        causing any error message containing ``stagnation`` to crash
        the recovery path with ``ValidationError``.  Using the
        ``_without_evidence`` variant clamps STAGNATION to UNKNOWN
        so construction succeeds.
        """
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        category = infer_failure_category_without_evidence(
            "Stagnation detected: repetitive tool calls"
        )
        # Must not raise: we're building a real RecoveryResult.
        result = _make_recovery_result(ctx, failure_category=category)
        assert result.failure_category is FailureCategory.UNKNOWN

    @pytest.mark.parametrize(
        "error_message",
        [
            "Stagnation detected: repetitive tool calls",
            "quality gate failed",
            "Acceptance criteria not satisfied",
            "Tool output quality degraded",
        ],
    )
    def test_no_validation_error_on_heuristic_keywords(
        self,
        sample_agent_context: AgentContext,
        error_message: str,
    ) -> None:
        """Regression for C1: evidence-requiring keywords must not crash."""
        ctx = sample_agent_context.with_task_transition(
            TaskStatus.IN_PROGRESS, reason="starting"
        )
        category = infer_failure_category_without_evidence(error_message)
        _make_recovery_result(ctx, failure_category=category)
