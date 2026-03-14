"""Tests for the error classification pipeline."""

from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest

from synthorg.budget.coordination_config import ErrorCategory, ErrorTaxonomyConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.engine.classification.pipeline import classify_execution_errors
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, ToolResult


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Pipeline Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
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


@pytest.mark.unit
class TestClassifyExecutionErrors:
    """classify_execution_errors pipeline function."""

    async def test_disabled_config_returns_none(self) -> None:
        config = ErrorTaxonomyConfig(enabled=False)
        result = await classify_execution_errors(
            _execution_result(),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is None

    async def test_clean_execution_returns_empty_findings(self) -> None:
        config = ErrorTaxonomyConfig(enabled=True)
        messages = (
            ChatMessage(role=MessageRole.SYSTEM, content="You are a helper."),
            ChatMessage(role=MessageRole.USER, content="Hello."),
            ChatMessage(role=MessageRole.ASSISTANT, content="Hi there."),
        )
        result = await classify_execution_errors(
            _execution_result(messages=messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.finding_count == 0
        assert result.has_findings is False
        assert result.agent_id == "agent-1"
        assert result.task_id == "task-1"

    async def test_only_enabled_categories_run(self) -> None:
        """Only COORDINATION_FAILURE is enabled — others should not run."""
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.COORDINATION_FAILURE,),
        )
        # Conversation has a contradiction but only coordination
        # failure is enabled — should not detect contradiction
        messages = (
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The cache is enabled for production.",
            ),
            ChatMessage(role=MessageRole.USER, content="Check."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The cache is not enabled for production.",
            ),
        )
        result = await classify_execution_errors(
            _execution_result(messages=messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.categories_checked == (ErrorCategory.COORDINATION_FAILURE,)
        # No coordination failures in this conversation
        assert result.finding_count == 0

    async def test_exception_handling_never_raises(self) -> None:
        """Pipeline catches regular exceptions and returns None."""
        config = ErrorTaxonomyConfig(enabled=True)
        with patch(
            "synthorg.engine.classification.pipeline._run_detectors",
            side_effect=RuntimeError("injected"),
        ):
            result = await classify_execution_errors(
                _execution_result(),
                "agent-1",
                "task-1",
                config=config,
            )
        assert result is None

    async def test_memory_error_propagates(self) -> None:
        """MemoryError propagates unconditionally."""
        config = ErrorTaxonomyConfig(enabled=True)
        with (
            patch(
                "synthorg.engine.classification.pipeline._run_detectors",
                side_effect=MemoryError,
            ),
            pytest.raises(MemoryError),
        ):
            await classify_execution_errors(
                _execution_result(),
                "agent-1",
                "task-1",
                config=config,
            )

    async def test_recursion_error_propagates(self) -> None:
        """RecursionError propagates unconditionally."""
        config = ErrorTaxonomyConfig(enabled=True)
        with (
            patch(
                "synthorg.engine.classification.pipeline._run_detectors",
                side_effect=RecursionError,
            ),
            pytest.raises(RecursionError),
        ):
            await classify_execution_errors(
                _execution_result(),
                "agent-1",
                "task-1",
                config=config,
            )

    async def test_coordination_failure_findings_are_high_severity(
        self,
    ) -> None:
        """Coordination failure findings should all be HIGH severity."""
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.COORDINATION_FAILURE,),
        )
        messages = (
            ChatMessage(role=MessageRole.ASSISTANT, content="Running tool."),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=_make_tool_error(),
            ),
        )
        result = await classify_execution_errors(
            _execution_result(messages=messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.finding_count >= 1
        for finding in result.findings:
            assert finding.severity == ErrorSeverity.HIGH

    async def test_result_has_correct_categories_checked(self) -> None:
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(
                ErrorCategory.LOGICAL_CONTRADICTION,
                ErrorCategory.NUMERICAL_DRIFT,
            ),
        )
        result = await classify_execution_errors(
            _execution_result(turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert set(result.categories_checked) == {
            ErrorCategory.LOGICAL_CONTRADICTION,
            ErrorCategory.NUMERICAL_DRIFT,
        }

    async def test_per_detector_isolation(self) -> None:
        """One broken detector should not prevent others from producing findings."""
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(
                ErrorCategory.LOGICAL_CONTRADICTION,
                ErrorCategory.COORDINATION_FAILURE,
            ),
        )
        messages = (
            ChatMessage(role=MessageRole.ASSISTANT, content="Running tool."),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=_make_tool_error(),
            ),
        )
        with patch(
            "synthorg.engine.classification.pipeline.detect_logical_contradictions",
            side_effect=RuntimeError("detector crash"),
        ):
            result = await classify_execution_errors(
                _execution_result(messages=messages, turns=(_turn(),)),
                "agent-1",
                "task-1",
                config=config,
            )
        assert result is not None
        # Coordination failure detector should still produce findings
        assert result.finding_count >= 1
        for finding in result.findings:
            assert finding.category == ErrorCategory.COORDINATION_FAILURE

    async def test_safe_detect_memory_error_propagates(self) -> None:
        """MemoryError from an individual detector propagates through _safe_detect."""
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(ErrorCategory.LOGICAL_CONTRADICTION,),
        )
        messages = (
            ChatMessage(role=MessageRole.ASSISTANT, content="Message one."),
            ChatMessage(role=MessageRole.USER, content="Continue."),
            ChatMessage(role=MessageRole.ASSISTANT, content="Message two."),
        )
        with (
            patch(
                "synthorg.engine.classification.pipeline.detect_logical_contradictions",
                side_effect=MemoryError,
            ),
            pytest.raises(MemoryError),
        ):
            await classify_execution_errors(
                _execution_result(messages=messages, turns=(_turn(),)),
                "agent-1",
                "task-1",
                config=config,
            )

    async def test_empty_categories_produces_no_findings(self) -> None:
        """Enabled config with empty categories tuple runs no detectors."""
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(),
        )
        messages = (
            ChatMessage(role=MessageRole.ASSISTANT, content="The cache is enabled."),
            ChatMessage(role=MessageRole.USER, content="Check."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The cache is not enabled.",
            ),
        )
        result = await classify_execution_errors(
            _execution_result(messages=messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.finding_count == 0
        assert result.categories_checked == ()


def _make_tool_error() -> ToolResult:
    """Create a ToolResult representing an error."""
    return ToolResult(
        tool_call_id="call-err-1",
        content="command not found",
        is_error=True,
    )
