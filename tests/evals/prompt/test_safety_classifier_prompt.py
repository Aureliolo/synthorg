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

    def test_temperature_is_zero(self) -> None:
        """Safety classifier must run at temperature=0 for a stable verdict.

        Uses an AST walk rather than substring matching so the check
        is resilient to formatting (``temperature=0.0`` vs
        ``temperature = 0.0``) and can't be fooled by a docstring
        or comment that merely mentions the phrase.
        """
        import ast

        from synthorg.security import safety_classifier

        source = inspect.getsource(safety_classifier)
        tree = ast.parse(source)

        def _is_zero(node: ast.AST) -> bool:
            # Reject bools explicitly -- ``isinstance(True, int)`` is
            # ``True`` in Python, so without this guard a
            # ``temperature=False`` binding would silently satisfy the
            # assertion.
            if not isinstance(node, ast.Constant):
                return False
            value = node.value
            if isinstance(value, bool):
                return False
            return isinstance(value, int | float) and value == 0

        # Restrict the search to ``CompletionConfig(..., temperature=0)``
        # calls so an unrelated module-level binding (e.g. a docstring
        # example assigned to ``temperature``) cannot make this test
        # false-pass. The classifier constructs its config via the
        # ``CompletionConfig`` class from ``synthorg.providers.models``.
        def _targets_completion_config(func: ast.AST) -> bool:
            if isinstance(func, ast.Name):
                return func.id == "CompletionConfig"
            if isinstance(func, ast.Attribute):
                return func.attr == "CompletionConfig"
            return False

        # Tighten the traversal: only walk the body of
        # ``_classify_via_llm`` (the single function that actually
        # builds the CompletionConfig) rather than the entire module,
        # so a CompletionConfig stubbed elsewhere in the file (tests,
        # helpers) cannot satisfy the assertion.
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
            and any(kw.arg == "temperature" and _is_zero(kw.value) for kw in n.keywords)
            for n in ast.walk(classify_fn)
        )
        assert found, (
            "safety_classifier must construct a ``CompletionConfig`` with "
            "``temperature=0.0`` inside ``_classify_via_llm``. No such "
            "call was found in that function's AST."
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
