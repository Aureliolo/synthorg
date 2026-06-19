"""Prompt eval: safety classifier temperature contract + verdict behaviour."""

import inspect

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.config import SafetyClassifierConfig
from synthorg.security.safety_classifier import (
    SafetyClassification,
    SafetyClassifier,
)
from tests._shared import FakeClock
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    run_grader,
)


def _verdict_response(*, classification: str) -> CompletionResponse:
    """A completion whose verdict tool call carries ``classification``."""
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id="tc-1",
                name="safety_classification_verdict",
                arguments={"classification": classification, "reason": "eval"},
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-small-001",
    )


def _no_verdict_response() -> CompletionResponse:
    """A completion that answers in prose without calling the verdict tool."""
    return CompletionResponse(
        content="This looks fine to me.",
        tool_calls=(),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-small-001",
    )


@pytest.mark.unit
class TestSafetyClassifierPromptContract:
    """Guard rails for the approval safety classifier prompt surface."""

    def test_sampling_defaults_are_deterministic(self) -> None:
        """The classifier config defaults to a deterministic verdict.

        ``temperature=0.0`` plus ``top_p=1.0`` (no nucleus truncation)
        is the canonical deterministic sampling pin.
        """
        config = SafetyClassifierConfig()
        assert config.temperature == 0.0
        assert config.top_p == 1.0

    def test_call_site_binds_config_sampling(self) -> None:
        """``_classify_via_llm`` binds temperature AND top_p from config.

        Uses an AST walk rather than substring matching so the check is
        resilient to formatting and cannot be fooled by a docstring. The
        sampling values are config-driven (not hardcoded), so the proof
        is that both kwargs read ``self._config.<field>``.
        """
        import ast

        from synthorg.security import safety_classifier

        tree = ast.parse(inspect.getsource(safety_classifier))

        def _targets_completion_config(func: ast.AST) -> bool:
            if isinstance(func, ast.Name):
                return func.id == "CompletionConfig"
            if isinstance(func, ast.Attribute):
                return func.attr == "CompletionConfig"
            return False

        def _reads_config_field(node: ast.expr, field: str) -> bool:
            # Match ``self._config.<field>``.
            return (
                isinstance(node, ast.Attribute)
                and node.attr == field
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "_config"
            )

        classify_fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == "_classify_via_llm"
            ):
                classify_fn = node
                break
        assert classify_fn is not None, (
            "safety_classifier must expose a ``_classify_via_llm`` function "
            "that drives the LLM call and pins its CompletionConfig."
        )

        found = any(
            isinstance(n, ast.Call)
            and _targets_completion_config(n.func)
            and any(
                kw.arg == "temperature" and _reads_config_field(kw.value, "temperature")
                for kw in n.keywords
            )
            and any(
                kw.arg == "top_p" and _reads_config_field(kw.value, "top_p")
                for kw in n.keywords
            )
            for n in ast.walk(classify_fn)
        )
        assert found, (
            "safety_classifier must construct a ``CompletionConfig`` binding "
            "both ``temperature=self._config.temperature`` and "
            "``top_p=self._config.top_p`` inside ``_classify_via_llm``."
        )


@pytest.mark.unit
class TestSafetyClassifierVerdictBehaviour:
    """Labelled eval for the verdict-parse contract.

    The prompt asks the LLM to call ``safety_classification_verdict``; this
    grades that the surface maps each valid verdict to the right
    ``SafetyClassification`` and fails SAFE-SIDE (``SUSPICIOUS``) when the
    verdict is missing or invalid -- the security-critical behaviour that
    fingerprint + temperature checks alone do not cover.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="safe_verdict_maps_safe",
            inp=_verdict_response(classification="safe"),
            expected=SafetyClassification.SAFE,
        ),
        LabelledExample(
            name="suspicious_verdict_maps_suspicious",
            inp=_verdict_response(classification="suspicious"),
            expected=SafetyClassification.SUSPICIOUS,
        ),
        LabelledExample(
            name="blocked_verdict_maps_blocked",
            inp=_verdict_response(classification="blocked"),
            expected=SafetyClassification.BLOCKED,
        ),
        LabelledExample(
            name="invalid_verdict_fails_safe_side",
            inp=_verdict_response(classification="dangerous"),
            expected=SafetyClassification.SUSPICIOUS,
        ),
        LabelledExample(
            name="missing_verdict_fails_safe_side",
            inp=_no_verdict_response(),
            expected=SafetyClassification.SUSPICIOUS,
        ),
    )

    def test_parse_response_matches_labelled_verdicts(self) -> None:
        """Every labelled (response, expected classification) pair grades."""
        classifier = SafetyClassifier(
            provider_registry=ProviderRegistry(drivers={}),
            provider_configs={},
            config=SafetyClassifierConfig(enabled=True),
            clock=FakeClock(),
        )

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, CompletionResponse)
            # Grade the verdict-parse seam directly: it is the deterministic
            # contract the prompt's tool schema is written against.
            result = classifier._parse_response(actual_input, "stripped", 0.0)
            return result.classification == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
