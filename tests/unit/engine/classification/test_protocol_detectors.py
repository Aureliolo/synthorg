"""Tests for protocol-level detectors."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.budget.coordination_config import (
    DetectionScope,
    ErrorCategory,
)
from synthorg.communication.delegation.models import DelegationRequest
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.engine.classification.protocol import (
    DetectionContext,
    Detector,
)
from synthorg.engine.classification.protocol_detectors import (
    AuthorityBreachDetector,
    DelegationProtocolDetector,
    ReviewPipelineProtocolDetector,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.review.models import (
    PipelineResult,
    ReviewStageResult,
    ReviewVerdict,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage
from tests._shared import as_uuid


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _execution_result(
    messages: tuple[ChatMessage, ...] = (),
    turns: tuple[TurnRecord, ...] = (),
) -> ExecutionResult:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    for msg in messages:
        ctx = ctx.with_message(msg)
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
    )


def _task(
    *,
    task_id: str = "task-1",
    parent_task_id: str | None = None,
    assigned_to: str | None = "agent-2",
    delegation_chain: tuple[str, ...] = (),
) -> Task:
    return Task(
        id=as_uuid(task_id),
        title="Test Task",
        description="A test task",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="agent-1",
        assigned_to=assigned_to,
        status=TaskStatus.IN_PROGRESS if assigned_to else TaskStatus.CREATED,
        parent_task_id=parent_task_id,
        delegation_chain=delegation_chain,
        estimated_complexity=Complexity.MEDIUM,
    )


def _turn(
    *,
    turn_number: int = 1,
    finish_reason: FinishReason = FinishReason.STOP,
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=100,
        output_tokens=50,
        cost=0.01,
        finish_reason=finish_reason,
    )


def _delegation_request(
    *,
    delegator_id: str = "agent-1",
    delegatee_id: str = "agent-2",
    task_id: str = "child-1",
    parent_task_id: str | None = "task-1",
) -> DelegationRequest:
    child_task = _task(
        task_id=task_id,
        parent_task_id=parent_task_id,
        assigned_to=delegatee_id,
        delegation_chain=(delegator_id,),
    )
    return DelegationRequest(
        delegator_id=delegator_id,
        delegatee_id=delegatee_id,
        task=child_task,
    )


def _review_result(
    *,
    task_id: str = "task-1",
    verdict: ReviewVerdict = ReviewVerdict.PASS,
    stages: tuple[ReviewStageResult, ...] = (),
) -> PipelineResult:
    return PipelineResult(
        task_id=task_id,
        final_verdict=verdict,
        stage_results=stages,
    )


# ── DelegationProtocolDetector ─────────────────────────────────


@pytest.mark.unit
class TestDelegationProtocolDetector:
    """DelegationProtocolDetector validation."""

    def test_implements_detector_protocol(self) -> None:
        assert isinstance(DelegationProtocolDetector(), Detector)

    def test_category(self) -> None:
        assert (
            DelegationProtocolDetector().category
            == ErrorCategory.DELEGATION_PROTOCOL_VIOLATION
        )

    def test_supported_scopes(self) -> None:
        scopes = DelegationProtocolDetector().supported_scopes
        assert DetectionScope.TASK_TREE in scopes
        assert DetectionScope.SAME_TASK not in scopes

    async def test_no_delegations_yields_empty(self) -> None:
        er = _execution_result()
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
        )
        findings = await DelegationProtocolDetector().detect(ctx)
        assert findings == ()

    async def test_valid_delegation_yields_empty(self) -> None:
        er = _execution_result()
        req = _delegation_request()
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            delegation_requests=(req,),
        )
        findings = await DelegationProtocolDetector().detect(ctx)
        assert findings == ()

    async def test_broken_delegation_chain_detected(self) -> None:
        """Delegation where parent_task_id is None flags a violation."""
        er = _execution_result()
        req = _delegation_request(parent_task_id=None)
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            delegation_requests=(req,),
        )
        findings = await DelegationProtocolDetector().detect(ctx)
        assert len(findings) >= 1
        assert all(
            f.category == ErrorCategory.DELEGATION_PROTOCOL_VIOLATION for f in findings
        )

    async def test_multiple_delegations_checked(self) -> None:
        """Multiple delegation requests are all validated."""
        er = _execution_result()
        # First is valid, second has broken chain (no parent_task_id)
        good = _delegation_request(parent_task_id="task-1")
        bad = _delegation_request(
            task_id="child-2",
            delegatee_id="agent-3",
            parent_task_id=None,
        )
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            delegation_requests=(good, bad),
        )
        findings = await DelegationProtocolDetector().detect(ctx)
        assert len(findings) >= 1
        # Only the broken one should produce a finding
        assert any(str(as_uuid("child-2")) in f.description for f in findings)


# ── ReviewPipelineProtocolDetector ─────────────────────────────


@pytest.mark.unit
class TestReviewPipelineProtocolDetector:
    """ReviewPipelineProtocolDetector validation."""

    def test_implements_detector_protocol(self) -> None:
        assert isinstance(ReviewPipelineProtocolDetector(), Detector)

    def test_category(self) -> None:
        assert (
            ReviewPipelineProtocolDetector().category
            == ErrorCategory.REVIEW_PIPELINE_VIOLATION
        )

    def test_supported_scopes(self) -> None:
        scopes = ReviewPipelineProtocolDetector().supported_scopes
        assert DetectionScope.TASK_TREE in scopes

    async def test_no_reviews_yields_empty(self) -> None:
        er = _execution_result()
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
        )
        findings = await ReviewPipelineProtocolDetector().detect(ctx)
        assert findings == ()

    async def test_pass_with_stages_yields_empty(self) -> None:
        er = _execution_result()
        review = _review_result(
            verdict=ReviewVerdict.PASS,
            stages=(
                ReviewStageResult(
                    stage_name="quality",
                    verdict=ReviewVerdict.PASS,
                ),
            ),
        )
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            review_results=(review,),
        )
        findings = await ReviewPipelineProtocolDetector().detect(ctx)
        assert findings == ()

    async def test_pass_without_stages_detected(self) -> None:
        """PASS verdict with no stage results is a protocol violation."""
        er = _execution_result()
        review = _review_result(
            verdict=ReviewVerdict.PASS,
            stages=(),
        )
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            review_results=(review,),
        )
        findings = await ReviewPipelineProtocolDetector().detect(ctx)
        assert len(findings) >= 1
        assert all(
            f.category == ErrorCategory.REVIEW_PIPELINE_VIOLATION for f in findings
        )

    async def test_inconsistent_verdict_detected(self) -> None:
        """PASS final verdict when a stage FAILed is a violation."""
        er = _execution_result()
        review = _review_result(
            verdict=ReviewVerdict.PASS,
            stages=(
                ReviewStageResult(
                    stage_name="quality",
                    verdict=ReviewVerdict.FAIL,
                    reason="Low quality",
                ),
            ),
        )
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            review_results=(review,),
        )
        findings = await ReviewPipelineProtocolDetector().detect(ctx)
        assert len(findings) >= 1


# ── AuthorityBreachDetector ────────────────────────────────────


@pytest.mark.unit
class TestAuthorityBreachDetector:
    """AuthorityBreachDetector validation."""

    def test_implements_detector_protocol(self) -> None:
        assert isinstance(AuthorityBreachDetector(), Detector)

    def test_category(self) -> None:
        assert (
            AuthorityBreachDetector().category == ErrorCategory.AUTHORITY_BREACH_ATTEMPT
        )

    def test_supported_scopes(self) -> None:
        scopes = AuthorityBreachDetector().supported_scopes
        assert DetectionScope.SAME_TASK in scopes

    async def test_no_tool_calls_yields_empty(self) -> None:
        messages = (
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="I completed the analysis.",
            ),
        )
        er = _execution_result(messages)
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        findings = await AuthorityBreachDetector().detect(ctx)
        assert findings == ()

    async def test_budget_overrun_detected(self) -> None:
        """Execution cost exceeding authority budget_limit is a breach."""
        identity = AgentIdentity(
            id=uuid4(),
            name="Test Agent",
            role="Developer",
            department="Engineering",
            model=ModelConfig(
                provider="test-provider",
                model_id="test-small-001",
            ),
            hiring_date=date(2026, 1, 1),
        )
        # Agent has authority with budget_limit=0.10
        # but execution cost is much higher
        ctx_obj = AgentContext.from_identity(identity)
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Done.",
        )
        ctx_obj = ctx_obj.with_message(msg)
        # Create turns that sum to > budget_limit
        turns = tuple(
            TurnRecord(
                turn_number=i + 1,
                input_tokens=10000,
                output_tokens=5000,
                cost=0.50,
                finish_reason=FinishReason.STOP,
            )
            for i in range(5)
        )
        er = ExecutionResult(
            context=ctx_obj,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        det_ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        detector = AuthorityBreachDetector(budget_limit=0.10)
        findings = await detector.detect(det_ctx)

        assert len(findings) >= 1
        assert all(
            f.category == ErrorCategory.AUTHORITY_BREACH_ATTEMPT for f in findings
        )
        assert all(f.severity == ErrorSeverity.HIGH for f in findings)

    async def test_denied_tool_invocation_from_turn_record(self) -> None:
        """Tool names in turn.tool_calls_made are matched against denied."""
        from synthorg.core.agent import ToolPermissions

        identity = AgentIdentity(
            id=uuid4(),
            name="Restricted Agent",
            role="Developer",
            department="Engineering",
            model=ModelConfig(
                provider="test-provider",
                model_id="test-small-001",
            ),
            hiring_date=date(2026, 1, 1),
            tools=ToolPermissions(denied=("delete_database", "wire_transfer")),
        )
        ctx_obj = AgentContext.from_identity(identity)
        ctx_obj = ctx_obj.with_message(
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Running a tool.",
            ),
        )
        turns = (
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.0,
                finish_reason=FinishReason.STOP,
                tool_calls_made=("delete_database",),
            ),
        )
        er = ExecutionResult(
            context=ctx_obj,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        det_ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        findings = await AuthorityBreachDetector().detect(det_ctx)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == ErrorSeverity.HIGH
        assert "delete_database" in finding.description
        assert "denied" in finding.description.lower()

    async def test_denied_tool_case_insensitive_match(self) -> None:
        """Tool denial matching casefolds on both sides."""
        from synthorg.core.agent import ToolPermissions

        identity = AgentIdentity(
            id=uuid4(),
            name="Restricted Agent",
            role="Developer",
            department="Engineering",
            model=ModelConfig(
                provider="test-provider",
                model_id="test-small-001",
            ),
            hiring_date=date(2026, 1, 1),
            tools=ToolPermissions(denied=("Delete_Database",)),
        )
        ctx_obj = AgentContext.from_identity(identity)
        ctx_obj = ctx_obj.with_message(
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Running it.",
            ),
        )
        turns = (
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.0,
                finish_reason=FinishReason.STOP,
                tool_calls_made=("DELETE_DATABASE",),
            ),
        )
        er = ExecutionResult(
            context=ctx_obj,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        det_ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        findings = await AuthorityBreachDetector().detect(det_ctx)
        assert len(findings) == 1

    async def test_denied_tool_deduplicated(self) -> None:
        """Multiple calls to the same denied tool produce one finding."""
        from synthorg.core.agent import ToolPermissions

        identity = AgentIdentity(
            id=uuid4(),
            name="Restricted Agent",
            role="Developer",
            department="Engineering",
            model=ModelConfig(
                provider="test-provider",
                model_id="test-small-001",
            ),
            hiring_date=date(2026, 1, 1),
            tools=ToolPermissions(denied=("forbidden",)),
        )
        ctx_obj = AgentContext.from_identity(identity)
        ctx_obj = ctx_obj.with_message(
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Calling.",
            ),
        )
        turns = (
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.0,
                finish_reason=FinishReason.STOP,
                tool_calls_made=("forbidden", "forbidden", "forbidden"),
            ),
        )
        er = ExecutionResult(
            context=ctx_obj,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        det_ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        findings = await AuthorityBreachDetector().detect(det_ctx)
        assert len(findings) == 1

    async def test_unauthorised_delegation_target(self) -> None:
        """Delegation target outside authority.can_delegate_to fires HIGH."""
        from synthorg.core.agent import ToolPermissions
        from synthorg.core.role import Authority

        identity = AgentIdentity(
            id=uuid4(),
            name="Manager",
            role="EngineeringManager",
            department="Engineering",
            model=ModelConfig(
                provider="test-provider",
                model_id="test-small-001",
            ),
            hiring_date=date(2026, 1, 1),
            tools=ToolPermissions(),
            authority=Authority(can_delegate_to=("Developer",)),
        )
        ctx_obj = AgentContext.from_identity(identity)
        er = ExecutionResult(
            context=ctx_obj,
            termination_reason=TerminationReason.COMPLETED,
        )
        unauthorised_task = _task(
            task_id="child-unauth",
            parent_task_id="task-1",
            assigned_to="Architect",
            delegation_chain=("agent-manager",),
        )
        req = DelegationRequest(
            delegator_id="agent-manager",
            delegatee_id="Architect",
            task=unauthorised_task,
        )
        det_ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-manager",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
            delegation_requests=(req,),
        )
        findings = await AuthorityBreachDetector().detect(det_ctx)
        assert len(findings) == 1
        assert "delegate" in findings[0].description.lower()
        assert findings[0].severity == ErrorSeverity.HIGH

    async def test_authority_budget_limit_from_identity(self) -> None:
        """When no explicit limit, identity.authority.budget_limit is used."""
        from synthorg.core.agent import ToolPermissions
        from synthorg.core.role import Authority

        identity = AgentIdentity(
            id=uuid4(),
            name="Budgeted Agent",
            role="Developer",
            department="Engineering",
            model=ModelConfig(
                provider="test-provider",
                model_id="test-small-001",
            ),
            hiring_date=date(2026, 1, 1),
            tools=ToolPermissions(),
            authority=Authority(budget_limit=0.05),
        )
        ctx_obj = AgentContext.from_identity(identity)
        ctx_obj = ctx_obj.with_message(
            ChatMessage(role=MessageRole.ASSISTANT, content="Done."),
        )
        turns = (
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.10,
                finish_reason=FinishReason.STOP,
            ),
        )
        er = ExecutionResult(
            context=ctx_obj,
            termination_reason=TerminationReason.COMPLETED,
            turns=turns,
        )
        det_ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        findings = await AuthorityBreachDetector().detect(det_ctx)
        assert len(findings) >= 1
        assert any("budget" in f.description.lower() for f in findings)
