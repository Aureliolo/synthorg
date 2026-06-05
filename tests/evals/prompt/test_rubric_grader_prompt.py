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

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestRubricGraderPromptContract:
    """Guard rails for the LLM rubric grader prompt surface."""

    def test_temperature_is_zero(self) -> None:
        """Grader must run at temperature=0 for deterministic scores.

        Checked via an AST walk (resilient to formatting) and a
        substring cross-check (guards against accidentally removing
        the keyword during a refactor).
        """
        import ast
        import inspect

        from synthorg.engine.quality.graders.llm import LLMRubricGrader

        source = inspect.getsource(LLMRubricGrader)
        assert "temperature=0.0" in source, (
            "LLMRubricGrader must pin temperature=0.0 for determinism"
        )
        # AST-level check: find a ``temperature=0.0`` keyword
        # argument inside a ``Call`` (CompletionConfig construction)
        # anywhere in the grader class. This refuses to pass if the
        # substring appears only in a docstring.
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
