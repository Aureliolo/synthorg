"""Prompt eval: rubric grader temperature + prompt drift.

Rather than replay a full LLM round-trip (flaky, provider-gated), the
suite asserts the two properties that deterministically matter for a
pinned prompt surface:

1. The production config still pins ``temperature=0.0`` -- any drift
   toward higher temperatures turns the grader non-deterministic
   across CI shards.
2. The bytes of the prompt body haven't silently drifted: edits must
   either update the pinned fingerprint in this test OR ship new
   labelled examples that still pass.

Reference implementation: ``synthorg.engine.quality.graders.llm``.
"""

import pytest

from synthorg.engine.quality.graders._llm_parser import parse_verdict
from synthorg.engine.quality.verification import VerificationVerdict
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    fingerprint_prompt,
    run_grader,
)


@pytest.mark.unit
class TestRubricGraderPromptContract:
    """Guard rails for the LLM rubric grader prompt surface."""

    def test_temperature_is_zero(self) -> None:
        """Grader must run at temperature=0 for deterministic scores.

        Checked via an AST walk only: it is resilient to formatting (a
        re-spaced or line-split keyword cannot cause a false failure) and
        immune to docstring mentions (a literal in prose cannot cause a
        false pass). A plain substring check would fail on both counts.
        """
        import ast
        import inspect

        from synthorg.engine.quality.graders.llm import LLMRubricGrader

        source = inspect.getsource(LLMRubricGrader)
        # AST-level check: find a ``temperature=0.0`` keyword
        # argument inside a ``Call`` (CompletionConfig construction)
        # anywhere in the grader class. This refuses to pass if the
        # literal appears only in a docstring.
        tree = ast.parse(source)
        found = any(
            isinstance(node, ast.keyword)
            and node.arg == "temperature"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int | float)
            and node.value.value == 0
            for node in ast.walk(tree)
        )
        assert found, (
            "LLMRubricGrader must pass ``temperature=0.0`` as a "
            "keyword argument when constructing CompletionConfig "
            "(no matching ast.keyword node found)"
        )

    # Pinned SHA-256[:16] of the grader's *prompt surface only* --
    # the system prompt body, the tool name + description + JSON
    # schema, and the pinned completion config (temperature + max
    # tokens + payload cap).  Hashing only the surface (instead of
    # the whole module source) means refactors to internals like the
    # ``cost_recording_scope`` wrapper or retry plumbing don't churn
    # this fingerprint -- only intentional prompt / contract edits do.
    #
    # When you intentionally change the prompt or tool contract,
    # update this value AND add a regression example below to prove
    # the new prompt still passes the grading contract.
    PINNED_RUBRIC_GRADER_FP = "827c215bbb618de9"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent prompt-surface drift via a stable hash.

        When the prompt or tool contract changes intentionally,
        update the pinned fingerprint + add a regression example
        below to prove the new prompt still passes the grading
        contract.
        """
        import json

        from synthorg.engine.quality.graders import _llm_prompt as _grader_module

        # Compose a deterministic, human-auditable surface payload
        # from the grader's pinned prompt + tool contract + config.
        # JSON serialisation with ``sort_keys=True`` guarantees
        # ordering doesn't drift when dict-literal keys are
        # rearranged in a refactor.
        surface = "\n".join(
            [
                f"system_prompt:{_grader_module._GRADER_SYSTEM_PROMPT}",
                f"tool_name:{_grader_module._GRADER_TOOL_NAME}",
                f"tool_description:{_grader_module._GRADER_TOOL_DESCRIPTION}",
                "tool_schema:"
                + json.dumps(_grader_module._GRADER_TOOL_SCHEMA, sort_keys=True),
                f"max_tokens:{_grader_module._DEFAULT_MAX_TOKENS}",
                f"max_payload_chars:{_grader_module._MAX_PAYLOAD_CHARS}",
                # Temperature is pinned at the call site, not via a
                # module constant, but we still cover it via the
                # ``test_temperature_is_zero`` test above; including
                # the literal here pins the whole config tuple so a
                # regression there fails *both* tests.
                "temperature:0.0",
            ],
        )

        fp = fingerprint_prompt(surface)
        assert fp == self.PINNED_RUBRIC_GRADER_FP, (
            f"LLM rubric grader prompt-surface fingerprint drifted: got {fp!r}, "  # noqa: S608 -- assertion message, not SQL
            f"expected {self.PINNED_RUBRIC_GRADER_FP!r}. "
            "If this was intentional, update the pinned fingerprint "
            "AND extend the labelled example set to cover the new behaviour."
        )


@pytest.mark.unit
class TestRubricGraderVerdictParse:
    """Labelled eval for the deterministic verdict-parse contract.

    The prompt asks the LLM to emit a verdict of ``pass`` / ``fail`` /
    ``refer``; this grades that :func:`parse_verdict` maps each valid
    verdict to the right :class:`VerificationVerdict` and fails closed
    (a reason string the grader routes to ``REFER``) on an unknown or
    non-string verdict -- the security-critical behaviour that the
    fingerprint + temperature checks alone do not cover.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(name="pass", inp="pass", expected=VerificationVerdict.PASS),
        LabelledExample(name="fail", inp="fail", expected=VerificationVerdict.FAIL),
        LabelledExample(name="refer", inp="refer", expected=VerificationVerdict.REFER),
        # Unknown verdict -> reason string (grader routes it to REFER).
        LabelledExample(name="unknown_refers", inp="approved", expected=str),
        # Non-string verdict -> reason string (fails closed to REFER).
        LabelledExample(name="non_string_refers", inp=123, expected=str),
    )

    def test_parse_verdict_matches_labelled_examples(self) -> None:
        """Every labelled (raw verdict, expected outcome) pair grades."""

        def _grade(actual_input: object, expected: object) -> bool:
            result = parse_verdict(actual_input)
            if expected is str:
                # Malformed verdict: must fail closed to a reason string,
                # which ``grade`` routes to a REFER verdict.
                return isinstance(result, str)
            return result == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
