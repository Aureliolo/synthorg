"""Tests for heuristic detector protocol wrappers."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.budget.coordination_config import (
    DetectionScope,
    ErrorCategory,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.classification.heuristic_detectors import (
    HeuristicContextOmissionDetector,
    HeuristicContradictionDetector,
    HeuristicCoordinationFailureDetector,
    HeuristicNumericalDriftDetector,
)
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.engine.classification.protocol import (
    DetectionContext,
    Detector,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolResult


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Heuristic Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _context(
    messages: tuple[ChatMessage, ...] = (),
    turns: tuple[TurnRecord, ...] = (),
) -> DetectionContext:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    for msg in messages:
        ctx = ctx.with_message(msg)
    er = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
    )
    return DetectionContext(
        execution_result=er,
        agent_id="agent-1",
        task_id="task-1",
        scope=DetectionScope.SAME_TASK,
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


def _assistant(content: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.ASSISTANT, content=content)


def _user(content: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=content)


@pytest.mark.unit
class TestHeuristicContradictionDetector:
    """HeuristicContradictionDetector protocol compliance and detection."""

    def test_implements_detector_protocol(self) -> None:
        detector = HeuristicContradictionDetector()
        assert isinstance(detector, Detector)

    def test_category(self) -> None:
        detector = HeuristicContradictionDetector()
        assert detector.category == ErrorCategory.LOGICAL_CONTRADICTION

    def test_supported_scopes(self) -> None:
        detector = HeuristicContradictionDetector()
        assert detector.supported_scopes == frozenset(
            {DetectionScope.SAME_TASK},
        )

    async def test_detects_contradiction(self) -> None:
        messages = (
            _assistant("The cache is enabled for production."),
            _user("Check."),
            _assistant("The cache is not enabled for production."),
        )
        ctx = _context(messages)
        detector = HeuristicContradictionDetector()
        findings = await detector.detect(ctx)

        assert len(findings) >= 1
        assert all(f.category == ErrorCategory.LOGICAL_CONTRADICTION for f in findings)
        assert all(f.severity == ErrorSeverity.HIGH for f in findings)

    async def test_no_contradiction_yields_empty(self) -> None:
        messages = (
            _assistant("The cache is enabled."),
            _user("Good."),
            _assistant("The cache handles 1000 req/s."),
        )
        ctx = _context(messages)
        detector = HeuristicContradictionDetector()
        findings = await detector.detect(ctx)

        assert findings == ()


@pytest.mark.unit
class TestHeuristicNumericalDriftDetector:
    """HeuristicNumericalDriftDetector protocol compliance and detection."""

    def test_implements_detector_protocol(self) -> None:
        detector = HeuristicNumericalDriftDetector()
        assert isinstance(detector, Detector)

    def test_category(self) -> None:
        detector = HeuristicNumericalDriftDetector()
        assert detector.category == ErrorCategory.NUMERICAL_DRIFT

    def test_supported_scopes(self) -> None:
        detector = HeuristicNumericalDriftDetector()
        assert detector.supported_scopes == frozenset(
            {DetectionScope.SAME_TASK},
        )

    async def test_detects_drift(self) -> None:
        messages = (
            _assistant("The total cost is 5000 USD."),
            _user("Double check."),
            _assistant("The total cost is 8000 USD."),
        )
        ctx = _context(messages)
        detector = HeuristicNumericalDriftDetector()
        findings = await detector.detect(ctx)

        assert len(findings) >= 1
        assert all(f.category == ErrorCategory.NUMERICAL_DRIFT for f in findings)

    async def test_custom_threshold(self) -> None:
        detector = HeuristicNumericalDriftDetector(threshold_percent=1.0)
        messages = (
            _assistant("The value is 100 tokens."),
            _user("Check."),
            _assistant("The value is 102 tokens."),
        )
        ctx = _context(messages)
        findings = await detector.detect(ctx)

        assert len(findings) >= 1


@pytest.mark.unit
class TestHeuristicContextOmissionDetector:
    """HeuristicContextOmissionDetector protocol compliance and detection."""

    def test_implements_detector_protocol(self) -> None:
        detector = HeuristicContextOmissionDetector()
        assert isinstance(detector, Detector)

    def test_category(self) -> None:
        detector = HeuristicContextOmissionDetector()
        assert detector.category == ErrorCategory.CONTEXT_OMISSION

    async def test_detects_omission(self) -> None:
        messages = (
            _assistant("AuthService and PaymentGateway handle this."),
            _user("Tell me more."),
            _assistant("AuthService uses JWT tokens."),
            _user("Continue."),
            _assistant("We deploy on a managed cluster."),
            _user("Monitoring?"),
            _assistant("We use metrics and dashboards."),
        )
        ctx = _context(messages)
        detector = HeuristicContextOmissionDetector()
        findings = await detector.detect(ctx)

        assert len(findings) >= 1
        descriptions = " ".join(f.description for f in findings)
        assert "PaymentGateway" in descriptions


@pytest.mark.unit
class TestHeuristicCoordinationFailureDetector:
    """HeuristicCoordinationFailureDetector protocol compliance and detection."""

    def test_implements_detector_protocol(self) -> None:
        detector = HeuristicCoordinationFailureDetector()
        assert isinstance(detector, Detector)

    def test_category(self) -> None:
        detector = HeuristicCoordinationFailureDetector()
        assert detector.category == ErrorCategory.COORDINATION_FAILURE

    async def test_detects_tool_error(self) -> None:
        messages = (
            _assistant("Running tool."),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(
                    tool_call_id="call-1",
                    content="error",
                    is_error=True,
                ),
            ),
        )
        ctx = _context(messages, turns=(_turn(),))
        detector = HeuristicCoordinationFailureDetector()
        findings = await detector.detect(ctx)

        assert len(findings) >= 1
        assert all(f.category == ErrorCategory.COORDINATION_FAILURE for f in findings)
        assert all(f.severity == ErrorSeverity.HIGH for f in findings)

    async def test_detects_error_finish_reason(self) -> None:
        messages = (_assistant("Working."),)
        turns = (_turn(finish_reason=FinishReason.ERROR),)
        ctx = _context(messages, turns=turns)
        detector = HeuristicCoordinationFailureDetector()
        findings = await detector.detect(ctx)

        assert len(findings) >= 1
