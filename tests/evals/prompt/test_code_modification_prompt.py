"""Prompt eval: code modification strategy temperature + prompt drift.

Verifies the deterministic-property contract for the meta self-
improvement code-modification strategy:

1. The CompletionConfig sent to the LLM picks temperature + max_tokens
   from ``CodeModificationConfig`` so they remain runtime-tunable
   (ops can tighten temperature in production), not silently hardcoded.
2. ``_SYSTEM_PROMPT`` bytes haven't drifted: silent prompt edits must
   either bump the pinned fingerprint here OR add a labelled regression
   example.

Reference: ``synthorg.meta.strategies.code_modification``.
"""

import ast
import inspect

import pytest

from tests.evals.prompt._harness import fingerprint_prompt

# Pinned SHA-256[:16] of ``_SYSTEM_PROMPT``. Bump when the prompt
# changes intentionally, paired with new behavioural coverage.
_PINNED_CODE_MOD_PROMPT_FP = "6ae360befb461ca9"


@pytest.mark.unit
class TestCodeModificationPromptContract:
    """Guard rails for the code modification strategy prompt surface."""

    def test_temperature_is_config_driven(self) -> None:
        """The CompletionConfig must read ``temperature`` from config.

        We deliberately avoid pinning the value to ``0.0`` here because
        operations may tune the temperature for code generation in
        production. The contract is "config-driven, not literal".

        Uses AST matching rather than substring search so a refactor
        that splits the keyword across lines, adds a comment, or aliases
        ``self._code_config`` cannot quietly slip past the gate.
        """
        from synthorg.meta.strategies.code_modification import (
            CodeModificationStrategy,
        )

        source = inspect.getsource(CodeModificationStrategy)
        tree = ast.parse(source)
        config_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CompletionConfig"
        ]
        assert config_calls, (
            "expected at least one CompletionConfig(...) call in "
            "CodeModificationStrategy"
        )
        call = config_calls[0]
        assert any(
            kw.arg == "temperature"
            and ast.unparse(kw.value) == "self._code_config.temperature"
            for kw in call.keywords
        ), (
            "CompletionConfig.temperature must come from "
            "self._code_config.temperature so the value stays runtime-"
            "tunable rather than hardcoded"
        )
        assert any(
            kw.arg == "max_tokens"
            and ast.unparse(kw.value) == "self._code_config.max_tokens"
            for kw in call.keywords
        ), (
            "CompletionConfig.max_tokens must come from "
            "self._code_config.max_tokens so token budgets stay tunable"
        )

    def test_system_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift in the code-modification ``_SYSTEM_PROMPT``."""
        from synthorg.meta.strategies import code_modification

        fp = fingerprint_prompt(code_modification._SYSTEM_PROMPT)
        assert fp == _PINNED_CODE_MOD_PROMPT_FP, (
            f"code_modification._SYSTEM_PROMPT drifted: got {fp!r}, "
            f"expected {_PINNED_CODE_MOD_PROMPT_FP!r}. Update the pin "
            "and add coverage for the new behaviour."
        )

    def test_system_prompt_includes_anti_injection_directive(self) -> None:
        """The prompt must include the untrusted-content directive.

        SynthOrg's prompt-safety contract requires every system prompt
        that consumes operator-controlled or task-derived text to
        include an explicit untrusted-content directive. The
        code-modification strategy formats triggering rules + signal
        contexts into the user message, so the directive is mandatory.
        """
        from synthorg.meta.strategies import code_modification

        # The exact directive bytes are produced by
        # ``untrusted_content_directive`` and may evolve, so we look
        # for the stable opening phrase rather than the full body.
        assert "untrusted" in code_modification._SYSTEM_PROMPT.lower(), (
            "_SYSTEM_PROMPT must include the untrusted-content "
            "directive (no 'untrusted' substring detected)"
        )
