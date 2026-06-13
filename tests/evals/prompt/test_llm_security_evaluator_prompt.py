"""Prompt eval: LLM security evaluator temperature contract + prompt drift.

``LlmSecurityEvaluator`` is a security-critical surface: it classifies actions
as allow / deny / escalate. The suite pins two deterministic properties so a
silent edit cannot weaken the guard:

1. The completion config draws its temperature from the evaluator's config
   (not a hardcoded literal), so an operator's determinism choice flows
   through.
2. The system prompt bytes have not drifted; an intentional edit must update
   the pinned fingerprint here.
"""

import inspect
from datetime import UTC, datetime

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.completion_enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.config import LlmFallbackConfig
from synthorg.security.llm_evaluator import LlmSecurityEvaluator
from synthorg.security.models import (
    EvaluationConfidence,
    SecurityVerdict,
    SecurityVerdictType,
)
from tests._shared import FakeClock
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
    run_grader,
)


def _verdict_response(*, verdict: str, risk_level: str = "low") -> CompletionResponse:
    """A completion whose ``security_verdict`` tool call carries *verdict*."""
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id="tc-1",
                name="security_verdict",
                arguments={
                    "verdict": verdict,
                    "risk_level": risk_level,
                    "reason": "eval",
                },
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-small-001",
    )


@pytest.mark.unit
class TestLlmSecurityEvaluatorPromptContract:
    """Guard rails for the LLM security evaluator prompt surface."""

    PINNED_FP = "0f0696f91435804a"

    def test_temperature_is_config_sourced(self) -> None:
        """Evaluator temperature must come from config, never a literal."""
        from synthorg.security import llm_evaluator

        source = inspect.getsource(llm_evaluator)
        assert completion_temperature_is_config_sourced(source), (
            "LlmSecurityEvaluator must build CompletionConfig with "
            "temperature drawn from self._config.temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the security evaluator system prompt."""
        from synthorg.security.llm_evaluator import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"LLM security evaluator system-prompt fingerprint drifted: got "
            f"{fp!r}, expected {self.PINNED_FP!r}. If intentional, update the "
            "pinned fingerprint AND re-confirm the allow/deny/escalate contract."
        )


@pytest.mark.unit
class TestLlmSecurityEvaluatorVerdictBehaviour:
    """Labelled eval for the allow/deny/escalate verdict-parse contract.

    The prompt's tool schema offers exactly three verdicts; this grades that
    the surface maps each ``security_verdict`` tool call to the matching
    ``SecurityVerdictType``, the contract a prompt edit must preserve.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="allow_maps_allow",
            inp=_verdict_response(verdict="allow"),
            expected=SecurityVerdictType.ALLOW,
        ),
        LabelledExample(
            name="deny_maps_deny",
            inp=_verdict_response(verdict="deny", risk_level="high"),
            expected=SecurityVerdictType.DENY,
        ),
        LabelledExample(
            name="escalate_maps_escalate",
            inp=_verdict_response(verdict="escalate", risk_level="medium"),
            expected=SecurityVerdictType.ESCALATE,
        ),
    )

    def test_parse_response_matches_labelled_verdicts(self) -> None:
        """Every labelled (response, expected verdict) pair grades."""
        evaluator = LlmSecurityEvaluator(
            provider_registry=ProviderRegistry(drivers={}),
            provider_configs={},
            config=LlmFallbackConfig(enabled=True),
            clock=FakeClock(),
        )
        rule_verdict = SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="No security rule triggered",
            risk_level=ApprovalRiskLevel.MEDIUM,
            confidence=EvaluationConfidence.LOW,
            evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
            evaluation_duration_ms=0.5,
        )

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, CompletionResponse)
            result = evaluator._parse_llm_response(actual_input, rule_verdict, 0.0)
            return result.verdict == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
