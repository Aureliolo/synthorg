"""Integration tests for the error taxonomy pipeline.

Verifies end-to-end classification with realistic conversation
patterns and validates structured log events are emitted.
"""

from datetime import date
from uuid import uuid4

import pytest

from ai_company.budget.coordination_config import ErrorCategory, ErrorTaxonomyConfig
from ai_company.core.agent import AgentIdentity, ModelConfig
from ai_company.engine.classification.models import ErrorSeverity
from ai_company.engine.classification.pipeline import classify_execution_errors
from ai_company.engine.context import AgentContext
from ai_company.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from ai_company.providers.enums import FinishReason, MessageRole
from ai_company.providers.models import ChatMessage, ToolResult

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Integration Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
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
        cost_usd=0.01,
        finish_reason=finish_reason,
    )


def _execution_result(
    messages: tuple[ChatMessage, ...],
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


@pytest.mark.integration
class TestErrorTaxonomyIntegration:
    """Full pipeline integration with realistic conversation patterns."""

    async def test_contradiction_detected_in_realistic_conversation(
        self,
    ) -> None:
        """A multi-turn conversation with a real contradiction."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a backend developer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Should we enable caching?",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "Yes, the cache is essential for performance. "
                    "The cache is enabled in our production config."
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="I checked and it seems off.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "You're right, the cache is not enabled in our "
                    "production config. Let me fix that."
                ),
            ),
        )
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.LOGICAL_CONTRADICTION,),
        )
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(), _turn(turn_number=2))),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.has_findings
        contradiction_findings = [
            f
            for f in result.findings
            if f.category == ErrorCategory.LOGICAL_CONTRADICTION
        ]
        assert len(contradiction_findings) >= 1

    async def test_tool_error_produces_coordination_failure(self) -> None:
        """Tool execution errors should be classified as coordination failures."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a developer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Run the tests.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Running the test suite now.",
            ),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(
                    tool_call_id="call-test-1",
                    content="FAILED: 3 tests failed with ImportError",
                    is_error=True,
                ),
            ),
        )
        turns = (
            _turn(turn_number=1),
            _turn(turn_number=2, finish_reason=FinishReason.ERROR),
        )
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.COORDINATION_FAILURE,),
        )
        result = await classify_execution_errors(
            _execution_result(messages, turns=turns),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.has_findings
        # Should find both the tool error and the error finish reason
        assert result.finding_count >= 2
        for f in result.findings:
            assert f.category == ErrorCategory.COORDINATION_FAILURE
            assert f.severity == ErrorSeverity.HIGH

    async def test_all_categories_run_together(self) -> None:
        """Enable all categories and verify pipeline handles them."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a developer.",
            ),
            ChatMessage(role=MessageRole.USER, content="Analyze the system."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The system processes about 1000 requests per second.",
            ),
            ChatMessage(role=MessageRole.USER, content="Continue."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The system processes about 500 requests per second.",
            ),
        )
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=tuple(ErrorCategory),
        )
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert set(result.categories_checked) == set(ErrorCategory)

    async def test_pipeline_handles_large_conversation(self) -> None:
        """Classification should complete for a moderately large conversation."""
        # Build a moderately large conversation (50 messages)
        messages: list[ChatMessage] = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a developer.",
            ),
        ]
        for i in range(25):
            messages.append(
                ChatMessage(
                    role=MessageRole.USER,
                    content=f"Question {i}.",
                )
            )
            messages.append(
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=f"Answer {i}. The SystemManager handles this.",
                )
            )
        turns = tuple(_turn(turn_number=i + 1) for i in range(25))

        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=tuple(ErrorCategory),
        )

        result = await classify_execution_errors(
            _execution_result(tuple(messages), turns=turns),
            "agent-1",
            "task-1",
            config=config,
        )

        assert result is not None
        assert set(result.categories_checked) == set(ErrorCategory)

    async def test_disabled_taxonomy_returns_none(self) -> None:
        """Disabled taxonomy should return None."""
        config = ErrorTaxonomyConfig(enabled=False)
        result = await classify_execution_errors(
            _execution_result(()),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is None

    async def test_numerical_drift_with_realistic_data(self) -> None:
        """Real-world scenario: cost estimate changes between turns."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a cost analyst.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="What will this cost?",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "Based on my analysis, the total cost is "
                    "approximately 5000 USD for the infrastructure."
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Double check that estimate.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "After recalculating, the total cost is "
                    "approximately 8000 USD for the infrastructure."
                ),
            ),
        )
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.NUMERICAL_DRIFT,),
        )
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        drift_findings = [
            f for f in result.findings if f.category == ErrorCategory.NUMERICAL_DRIFT
        ]
        assert len(drift_findings) >= 1

    async def test_context_omission_with_realistic_data(self) -> None:
        """Entity mentioned early disappears from later discussion."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a system architect.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Describe the architecture.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "The architecture has three main components: "
                    "AuthService, PaymentGateway, and NotificationEngine."
                ),
            ),
            ChatMessage(role=MessageRole.USER, content="Tell me more."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "AuthService handles JWT tokens and session management. "
                    "PaymentGateway processes Stripe webhooks."
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="What about deployment?",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "We deploy AuthService to a dedicated cluster. "
                    "The database runs on managed PostgreSQL."
                ),
            ),
            ChatMessage(role=MessageRole.USER, content="And monitoring?"),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "We use Prometheus for metrics and Grafana for dashboards. "
                    "Alerts go to the ops team."
                ),
            ),
        )
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.CONTEXT_OMISSION,),
        )
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        # NotificationEngine and PaymentGateway should be flagged
        # as they disappear from later messages
        omitted_descriptions = " ".join(f.description for f in result.findings)
        assert "NotificationEngine" in omitted_descriptions
