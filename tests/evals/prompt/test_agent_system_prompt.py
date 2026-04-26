"""Prompt eval: agent system-prompt builder determinism.

The agent system prompt is composed from an ``AgentIdentity`` object
plus static fence directives (untrusted-content, SEC-1). This test
pins the fingerprint of the directives so silent edits to the
``wrap_untrusted`` / ``untrusted_content_directive`` surface are
caught before they land.
"""

import inspect

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestAgentSystemPromptContract:
    """Guard rails for the agent system prompt composition."""

    # Pinned SHA-256[:16] of ``synthorg.engine.prompt_safety``. Bump
    # this deliberately when the untrusted-content fence directive or
    # any tag-escaping logic changes -- a drift here means the SEC-1
    # contract has moved and dependent call sites must be re-audited.
    PINNED_PROMPT_SAFETY_FP = "fd3a7f2cf02996c3"

    def test_prompt_safety_fingerprint_stable(self) -> None:
        """Detect silent edits to the untrusted-content fence directive."""
        from synthorg.engine import prompt_safety

        source = inspect.getsource(prompt_safety)
        fp = fingerprint_prompt(source)
        assert fp == self.PINNED_PROMPT_SAFETY_FP, (
            f"prompt_safety source fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_PROMPT_SAFETY_FP!r}. "
            "If this was intentional, update the pinned fingerprint "
            "and re-audit every SEC-1 call site that wraps untrusted "
            "content via ``wrap_untrusted``."
        )
