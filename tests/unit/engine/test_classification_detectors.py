"""Tests for coordination error detectors."""

import pytest

from synthorg.budget.coordination_config import ErrorCategory
from synthorg.engine.classification.detectors import (
    detect_context_omissions,
    detect_coordination_failures,
    detect_logical_contradictions,
    detect_numerical_drift,
)
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.engine.loop_protocol import TurnRecord
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, ToolResult


def _assistant(content: str) -> ChatMessage:
    """Create an assistant message with the given content."""
    return ChatMessage(role=MessageRole.ASSISTANT, content=content)


def _user(content: str) -> ChatMessage:
    """Create a user message with the given content."""
    return ChatMessage(role=MessageRole.USER, content=content)


def _tool_msg(
    *,
    tool_call_id: str = "call-1",
    content: str = "ok",
    is_error: bool = False,
) -> ChatMessage:
    """Create a tool result message."""
    return ChatMessage(
        role=MessageRole.TOOL,
        tool_result=ToolResult(
            tool_call_id=tool_call_id,
            content=content,
            is_error=is_error,
        ),
    )


def _turn(
    *,
    turn_number: int = 1,
    finish_reason: FinishReason = FinishReason.STOP,
) -> TurnRecord:
    """Create a minimal TurnRecord."""
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
        finish_reason=finish_reason,
    )


@pytest.mark.unit
class TestDetectLogicalContradictions:
    """detect_logical_contradictions function."""

    def test_clean_conversation_returns_empty(self) -> None:
        conversation = (
            _assistant("The system is running smoothly."),
            _user("Great, continue."),
            _assistant("The system is running smoothly indeed."),
        )
        result = detect_logical_contradictions(conversation)
        assert result == ()

    def test_is_true_vs_is_not_true(self) -> None:
        conversation = (
            _assistant("The cache is enabled for production."),
            _user("Are you sure?"),
            _assistant("The cache is not enabled for production."),
        )
        result = detect_logical_contradictions(conversation)
        assert len(result) >= 1
        assert result[0].category == ErrorCategory.LOGICAL_CONTRADICTION
        assert result[0].severity == ErrorSeverity.HIGH

    def test_should_vs_should_not(self) -> None:
        conversation = (
            _assistant("We should use connection pooling here."),
            _user("Why?"),
            _assistant("We should not use connection pooling here."),
        )
        result = detect_logical_contradictions(conversation)
        assert len(result) >= 1
        assert result[0].category == ErrorCategory.LOGICAL_CONTRADICTION

    def test_single_message_returns_empty(self) -> None:
        conversation = (_assistant("Everything is fine."),)
        result = detect_logical_contradictions(conversation)
        assert result == ()

    @pytest.mark.parametrize(
        ("affirm", "negate"),
        [
            ("The API is stable", "The API is not stable"),
            ("The service should retry", "The service should not retry"),
            ("The result was correct", "The result was not correct"),
        ],
    )
    def test_parametrized_negation_patterns(self, affirm: str, negate: str) -> None:
        conversation = (
            _assistant(f"{affirm} in this context."),
            _user("Continue."),
            _assistant(f"{negate} in this context."),
        )
        result = detect_logical_contradictions(conversation)
        assert len(result) >= 1
        finding = result[0]
        assert finding.category == ErrorCategory.LOGICAL_CONTRADICTION
        assert finding.evidence
        assert finding.turn_range is not None

    def test_non_assistant_messages_ignored(self) -> None:
        conversation = (
            _user("The cache is enabled."),
            _assistant("I understand."),
            _user("The cache is not enabled."),
            _assistant("Got it."),
        )
        result = detect_logical_contradictions(conversation)
        assert result == ()

    def test_multiple_contradictions_detected(self) -> None:
        """Multiple distinct contradictions produce multiple findings."""
        conversation = (
            _assistant(
                "The cache is enabled for production. "
                "The service should retry on failure."
            ),
            _user("Are you sure?"),
            _assistant(
                "The cache is not enabled for production. "
                "The service should not retry on failure."
            ),
        )
        result = detect_logical_contradictions(conversation)
        assert len(result) >= 2


@pytest.mark.unit
class TestDetectNumericalDrift:
    """detect_numerical_drift function."""

    def test_consistent_numbers_returns_empty(self) -> None:
        conversation = (
            _assistant("The latency is approximately 100 ms."),
            _user("And now?"),
            _assistant("The latency is approximately 100 ms."),
        )
        result = detect_numerical_drift(conversation)
        assert result == ()

    def test_drift_detected(self) -> None:
        conversation = (
            _assistant("The response time is about 100 ms."),
            _user("Check again."),
            _assistant("The response time is about 200 ms."),
        )
        result = detect_numerical_drift(conversation)
        assert len(result) >= 1
        assert result[0].category == ErrorCategory.NUMERICAL_DRIFT
        assert result[0].severity in (ErrorSeverity.MEDIUM, ErrorSeverity.HIGH)

    def test_custom_threshold(self) -> None:
        conversation = (
            _assistant("The cost was about 100 USD."),
            _user("And the updated cost?"),
            _assistant("The cost was about 103 USD."),
        )
        # 3% drift — default 5% threshold should NOT flag it
        result_default = detect_numerical_drift(conversation)
        # Check that this specific pair is not flagged at default threshold
        cost_findings = [f for f in result_default if "cost" in f.description.lower()]
        assert len(cost_findings) == 0

        # 1% threshold SHOULD flag it
        result_strict = detect_numerical_drift(conversation, threshold_percent=1.0)
        assert len(result_strict) >= 1

    def test_no_numbers_returns_empty(self) -> None:
        conversation = (
            _assistant("Everything looks good."),
            _user("Continue."),
            _assistant("All done."),
        )
        result = detect_numerical_drift(conversation)
        assert result == ()

    def test_boundary_at_exactly_threshold(self) -> None:
        """Exactly 5% drift should NOT be flagged (> not >=)."""
        conversation = (
            _assistant("The count is exactly 100 tokens."),
            _user("And now?"),
            _assistant("The count is exactly 105 tokens."),
        )
        result = detect_numerical_drift(conversation, threshold_percent=5.0)
        # 5% drift is exactly at threshold — should NOT be flagged
        count_findings = [f for f in result if "count" in f.description.lower()]
        assert len(count_findings) == 0

    def test_large_drift_is_high_severity(self) -> None:
        conversation = (
            _assistant("The budget is about 100 USD."),
            _user("Check again."),
            _assistant("The budget is about 200 USD."),
        )
        result = detect_numerical_drift(conversation)
        high_findings = [f for f in result if f.severity == ErrorSeverity.HIGH]
        assert len(high_findings) >= 1

    def test_zero_to_nonzero_detects_drift(self) -> None:
        """Zero baseline to non-zero value should detect drift."""
        conversation = (
            _assistant("The error count is 0 tokens."),
            _user("And now?"),
            _assistant("The error count is 5 tokens."),
        )
        result = detect_numerical_drift(conversation)
        drift_findings = [f for f in result if "count" in f.description.lower()]
        assert len(drift_findings) >= 1

    def test_zero_to_zero_no_drift(self) -> None:
        """Both zero values should not detect drift."""
        conversation = (
            _assistant("The error count is 0 tokens."),
            _user("And now?"),
            _assistant("The error count is 0 tokens."),
        )
        result = detect_numerical_drift(conversation)
        count_findings = [f for f in result if "count" in f.description.lower()]
        assert len(count_findings) == 0


@pytest.mark.unit
class TestDetectContextOmissions:
    """detect_context_omissions function."""

    def test_all_entities_referenced_returns_empty(self) -> None:
        conversation = (
            _assistant("The AuthService handles login."),
            _user("Tell me more."),
            _assistant("The AuthService also handles token refresh."),
            _user("What else?"),
            _assistant("The AuthService validates JWTs too."),
            _user("Continue."),
            _assistant("AuthService is fully tested."),
        )
        result = detect_context_omissions(conversation)
        assert result == ()

    def test_entity_dropped_from_later_turns(self) -> None:
        conversation = (
            _assistant("The AuthService and CacheManager are critical."),
            _user("Explain."),
            _assistant("AuthService handles authentication flows."),
            _user("What about the rest?"),
            _assistant("The database layer handles persistence."),
            _user("Continue."),
            _assistant("The database layer is fully tested."),
        )
        result = detect_context_omissions(conversation)
        # CacheManager should be flagged as omitted
        omitted_entities = {f.description for f in result}
        assert any("CacheManager" in d for d in omitted_entities)

    def test_short_conversation_returns_empty(self) -> None:
        """Need at least 4 assistant messages."""
        conversation = (
            _assistant("The AuthService is important."),
            _user("Tell me more."),
            _assistant("It handles login."),
        )
        result = detect_context_omissions(conversation)
        assert result == ()

    def test_no_entities_returns_empty(self) -> None:
        conversation = (
            _assistant("the system works well."),
            _user("Continue."),
            _assistant("it handles all cases."),
            _user("More."),
            _assistant("everything is tested."),
            _user("And?"),
            _assistant("all done now."),
        )
        result = detect_context_omissions(conversation)
        assert result == ()

    def test_common_capitalised_words_not_flagged(self) -> None:
        """Words like 'True', 'None', 'Each' should be filtered out."""
        conversation = (
            _assistant("True enough. None of these apply. Each step matters."),
            _user("Continue."),
            _assistant("True again. None remain. Each is handled."),
            _user("More."),
            _assistant("The system works perfectly."),
            _user("And?"),
            _assistant("All done now."),
        )
        result = detect_context_omissions(conversation)
        flagged_descriptions = " ".join(f.description for f in result)
        assert "True" not in flagged_descriptions
        assert "None" not in flagged_descriptions
        assert "Each" not in flagged_descriptions

    def test_findings_have_correct_category(self) -> None:
        conversation = (
            _assistant("The PaymentGateway processes transactions."),
            _user("Explain."),
            _assistant("PaymentGateway validates card numbers."),
            _user("What next?"),
            _assistant("The system returns a response."),
            _user("Continue."),
            _assistant("The response is logged."),
        )
        result = detect_context_omissions(conversation)
        for finding in result:
            assert finding.category == ErrorCategory.CONTEXT_OMISSION
            assert finding.severity == ErrorSeverity.MEDIUM


@pytest.mark.unit
class TestDetectCoordinationFailures:
    """detect_coordination_failures function."""

    def test_no_errors_returns_empty(self) -> None:
        conversation = (
            _assistant("Running the tool."),
            _tool_msg(content="success"),
        )
        turns = (_turn(),)
        result = detect_coordination_failures(conversation, turns)
        assert result == ()

    def test_tool_execution_error_detected(self) -> None:
        conversation = (
            _assistant("Running the tool."),
            _tool_msg(content="command not found", is_error=True),
        )
        turns = (_turn(),)
        result = detect_coordination_failures(conversation, turns)
        assert len(result) >= 1
        assert result[0].category == ErrorCategory.COORDINATION_FAILURE
        assert result[0].severity == ErrorSeverity.HIGH

    def test_error_finish_reason_detected(self) -> None:
        conversation = (_assistant("Processing."),)
        turns = (_turn(finish_reason=FinishReason.ERROR),)
        result = detect_coordination_failures(conversation, turns)
        assert len(result) >= 1
        finding = result[0]
        assert finding.category == ErrorCategory.COORDINATION_FAILURE
        assert "finish_reason" in finding.evidence[0]
        assert finding.turn_range == (0, 0)

    def test_error_finish_reason_uses_turn_index(self) -> None:
        """turn_range uses 0-based index into turns tuple."""
        conversation = (_assistant("Processing."),)
        turns = (
            _turn(turn_number=1),
            _turn(turn_number=2, finish_reason=FinishReason.ERROR),
        )
        result = detect_coordination_failures(conversation, turns)
        error_findings = [f for f in result if "finish_reason" in f.evidence[0]]
        assert len(error_findings) == 1
        assert error_findings[0].turn_range == (1, 1)

    def test_empty_turns_returns_empty(self) -> None:
        conversation = (_assistant("Hello."),)
        turns: tuple[TurnRecord, ...] = ()
        result = detect_coordination_failures(conversation, turns)
        assert result == ()

    def test_combined_tool_errors_and_error_finish_reasons(self) -> None:
        """Both tool errors and error finish reasons contribute findings."""
        conversation = (
            _assistant("Running."),
            _tool_msg(content="err", is_error=True),
        )
        turns = (_turn(finish_reason=FinishReason.ERROR),)
        result = detect_coordination_failures(conversation, turns)
        assert len(result) >= 2

    def test_multiple_tool_errors(self) -> None:
        conversation = (
            _assistant("Step 1."),
            _tool_msg(
                tool_call_id="call-1",
                content="error 1",
                is_error=True,
            ),
            _assistant("Step 2."),
            _tool_msg(
                tool_call_id="call-2",
                content="error 2",
                is_error=True,
            ),
        )
        turns = (_turn(), _turn(turn_number=2))
        result = detect_coordination_failures(conversation, turns)
        assert len(result) >= 2


@pytest.mark.unit
class TestDetectorsEmptyConversation:
    """All detectors handle empty conversation gracefully."""

    def test_logical_contradictions_empty(self) -> None:
        assert detect_logical_contradictions(()) == ()

    def test_numerical_drift_empty(self) -> None:
        assert detect_numerical_drift(()) == ()

    def test_context_omissions_empty(self) -> None:
        assert detect_context_omissions(()) == ()

    def test_coordination_failures_empty(self) -> None:
        assert detect_coordination_failures((), ()) == ()
