"""Prompt eval: semantic error detectors temperature + prompt drift.

The three concrete semantic detectors share a base that pins
``temperature=0.0`` by default (deterministic classification) and a pinned
``max_tokens``. Each subclass builds its own analysis prompt; all three are
fingerprinted against silent drift over a fixed conversation input.
"""

import inspect

import pytest

from tests.evals.prompt._harness import fingerprint_prompt

_FIXED_CONVERSATION = "<<CONVERSATION>>"


@pytest.mark.unit
class TestSemanticDetectorPromptContract:
    """Guard rails for the semantic error detector prompt surfaces."""

    PINNED_CONTRADICTION_FP = "174e039dc83e62c4"
    PINNED_NUMERICAL_FP = "33a62df5171dedae"
    PINNED_MISSING_REF_FP = "4da85c646c838321"

    def test_default_temperature_is_zero(self) -> None:
        """The detector base must default to temperature=0.0."""
        from synthorg.engine.classification.semantic_detectors import (
            _BaseSemanticDetector,
        )

        default = (
            inspect.signature(
                _BaseSemanticDetector.__init__,
            )
            .parameters["temperature"]
            .default
        )
        assert default == 0.0, (
            "_BaseSemanticDetector must default temperature to 0.0 for "
            "deterministic detection."
        )

    def test_contradiction_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the contradiction detector prompt."""
        from synthorg.engine.classification.semantic_detectors import (
            SemanticContradictionDetector,
        )

        inst = SemanticContradictionDetector.__new__(SemanticContradictionDetector)
        fp = fingerprint_prompt(inst._prompt(_FIXED_CONVERSATION))
        assert fp == self.PINNED_CONTRADICTION_FP, (
            f"contradiction detector prompt drifted: {fp!r} != "
            f"{self.PINNED_CONTRADICTION_FP!r}. Update the pin if intentional."
        )

    def test_numerical_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the numerical verification detector prompt."""
        from synthorg.engine.classification.semantic_detectors import (
            SemanticNumericalVerificationDetector,
        )

        inst = SemanticNumericalVerificationDetector.__new__(
            SemanticNumericalVerificationDetector,
        )
        fp = fingerprint_prompt(inst._prompt(_FIXED_CONVERSATION))
        assert fp == self.PINNED_NUMERICAL_FP, (
            f"numerical detector prompt drifted: {fp!r} != "
            f"{self.PINNED_NUMERICAL_FP!r}. Update the pin if intentional."
        )

    def test_missing_reference_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the missing-reference detector prompt."""
        from synthorg.engine.classification.semantic_detectors import (
            SemanticMissingReferenceDetector,
        )

        inst = SemanticMissingReferenceDetector.__new__(
            SemanticMissingReferenceDetector,
        )
        fp = fingerprint_prompt(inst._prompt(_FIXED_CONVERSATION))
        assert fp == self.PINNED_MISSING_REF_FP, (
            f"missing-reference detector prompt drifted: {fp!r} != "
            f"{self.PINNED_MISSING_REF_FP!r}. Update the pin if intentional."
        )
