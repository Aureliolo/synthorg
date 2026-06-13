"""Shared harness for prompt-surface evaluation suites.

Goals:

- **Deterministic CI**: pin ``temperature=0.0`` and a ``ScriptedProvider``
  so assertions are reproducible across runs.
- **Drift detection**: snapshot the exact system prompt bytes used by
  each production surface so prompt edits fail fast.
- **Grade thresholds**: every suite asserts accuracy >= threshold on
  a labelled example set so prompt regressions do not pass silently.
"""

import ast
import hashlib
from collections.abc import Callable
from dataclasses import dataclass


def _is_completion_config(func: ast.expr) -> bool:
    """Whether *func* names the ``CompletionConfig`` constructor."""
    if isinstance(func, ast.Name):
        return func.id == "CompletionConfig"
    if isinstance(func, ast.Attribute):
        return func.attr == "CompletionConfig"
    return False


def _completion_temperature_values(source: str) -> list[ast.expr]:
    """Every ``temperature=`` value node passed to a ``CompletionConfig(...)``.

    Used by the prompt-eval suites to assert the temperature contract via an
    AST walk (resilient to formatting, immune to docstring mentions) rather
    than substring matching.
    """
    tree = ast.parse(source)
    values: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_completion_config(node.func):
            values.extend(kw.value for kw in node.keywords if kw.arg == "temperature")
    return values


def completion_temperature_is_literal(source: str, expected: float) -> bool:
    """True if a ``CompletionConfig`` in *source* pins ``temperature=expected``.

    Rejects ``bool`` constants explicitly: ``isinstance(True, int)`` is True in
    Python, so ``temperature=False`` would otherwise satisfy ``== 0``.
    """
    for value in _completion_temperature_values(source):
        if not isinstance(value, ast.Constant):
            continue
        literal = value.value
        if isinstance(literal, bool):
            continue
        if isinstance(literal, int | float) and literal == expected:
            return True
    return False


def completion_temperature_is_config_sourced(source: str) -> bool:
    """True if every ``CompletionConfig`` temperature is drawn from an attribute.

    Confirms the surface sources its temperature from a config object
    (``self._config.temperature``) rather than a hardcoded literal, so a model
    / config change flows through. Fails when no ``CompletionConfig`` call is
    found (a miswired test) or when any temperature is a bare literal.
    """
    values = _completion_temperature_values(source)
    if not values:
        return False
    return all(isinstance(value, ast.Attribute) for value in values)


@dataclass(frozen=True)
class LabelledExample:
    """One input + expected-output pair for prompt grading."""

    name: str
    inp: object
    expected: object


@dataclass(frozen=True)
class EvalOutcome:
    """Result of running a prompt surface against a labelled set."""

    total: int
    passed: int
    failures: tuple[str, ...]

    @property
    def accuracy(self) -> float:
        """Fraction of examples where the surface matched expected."""
        if self.total == 0:
            return 1.0
        return self.passed / self.total


def fingerprint_prompt(prompt: str) -> str:
    """Return a short SHA-256 hex digest for a prompt body.

    Suites use this to assert the shipped prompt has not drifted
    silently: a mismatch signals that an edit was made without
    updating the pinned fingerprint + labelled examples.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def run_grader(
    examples: tuple[LabelledExample, ...],
    grade: Callable[[object, object], bool],
) -> EvalOutcome:
    """Run ``grade(actual_input, expected)`` across the example set."""
    failures: list[str] = []
    passed = 0
    for ex in examples:
        if grade(ex.inp, ex.expected):
            passed += 1
        else:
            failures.append(ex.name)
    return EvalOutcome(
        total=len(examples),
        passed=passed,
        failures=tuple(failures),
    )


def assert_accuracy_at_least(outcome: EvalOutcome, threshold: float) -> None:
    """Fail the test if ``outcome.accuracy`` is below ``threshold``.

    Also fails fast when ``outcome.total == 0`` (no examples were
    run) so a miswired suite cannot silently report success, and
    rejects thresholds outside the valid probability range so a
    typo like ``-0.9`` or ``90`` cannot silently turn the contract
    into "always passes" / "never passes".
    """
    if not 0.0 <= threshold <= 1.0:
        msg = f"threshold must be between 0.0 and 1.0 inclusive; got {threshold!r}"
        raise ValueError(msg)
    if outcome.total == 0:
        msg = (
            "prompt eval ran zero labelled examples -- suite may be "
            "miswired (empty example set, import failure, or skipped "
            "branch). Refusing to pass silently."
        )
        raise AssertionError(msg)
    if outcome.accuracy < threshold:
        failed = ", ".join(outcome.failures[:5])
        msg = (
            f"prompt eval accuracy {outcome.accuracy:.2%} "
            f"below threshold {threshold:.2%}; "
            f"{outcome.total - outcome.passed}/{outcome.total} failed. "
            f"First failures: {failed}"
        )
        raise AssertionError(msg)
