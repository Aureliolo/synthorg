"""Prompt eval: procedural-memory proposer temperature + prompt drift.

Verifies the deterministic-property contract for the procedural-memory
proposer LLM call:

1. The CompletionConfig built at construction time reads
   ``temperature`` + ``max_tokens`` from ``ProceduralMemoryConfig`` so
   the values stay runtime-tunable rather than silently hardcoded.
2. ``_SYSTEM_PROMPT`` bytes haven't drifted: silent prompt edits must
   either bump the pinned fingerprint here OR ship new behavioural
   coverage proving the new prompt still satisfies the contract.
3. The proposer passes its module-level ``self._completion_config`` to
   ``provider.complete``; a future refactor that constructs a fresh
   CompletionConfig inline fails the call-site assertion.

Reference: ``synthorg.memory.procedural.proposer``.
"""

import ast
import inspect
import textwrap

import pytest

from tests.evals.prompt._harness import fingerprint_prompt

# Pinned SHA-256[:16] of ``_SYSTEM_PROMPT``. Bump when the prompt
# changes intentionally, paired with new behavioural coverage.
_PINNED_PROCEDURAL_PROMPT_FP = "c1cdefcde471733a"


@pytest.mark.unit
class TestProceduralMemoryPromptContract:
    """Guard rails for the procedural-memory proposer prompt surface."""

    def test_completion_config_is_built_from_config(self) -> None:
        """The proposer must build CompletionConfig from runtime config.

        We deliberately don't pin the value to ``0.0``: ops can tune
        the temperature for failure analysis. The contract is that
        the CompletionConfig is built from ``ProceduralMemoryConfig``,
        not hardcoded.

        Uses AST matching rather than substring search so a refactor
        that splits the keyword across lines, adds a comment, or
        aliases ``config`` cannot quietly slip past the gate.
        """
        from synthorg.memory.procedural.proposer import ProceduralMemoryProposer

        source = inspect.getsource(ProceduralMemoryProposer)
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
            "ProceduralMemoryProposer"
        )

        def _kw(call: ast.Call, name: str) -> str | None:
            for kw in call.keywords:
                if kw.arg == name:
                    return ast.unparse(kw.value)
            return None

        # Match against any CompletionConfig call carrying BOTH
        # required kwargs.  Selecting ``config_calls[0]`` would
        # silently break if a future refactor introduced a second
        # CompletionConfig earlier in the class (e.g. for a retry
        # path), so search the full set.
        assert any(
            _kw(call, "temperature") == "config.temperature"
            and _kw(call, "max_tokens") == "config.max_tokens"
            for call in config_calls
        ), (
            "ProceduralMemoryProposer must build CompletionConfig from "
            "config.temperature and config.max_tokens so values remain "
            "runtime-tunable"
        )

    def test_system_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift in the proposer ``_SYSTEM_PROMPT``."""
        from synthorg.memory.procedural import proposer

        fp = fingerprint_prompt(proposer._SYSTEM_PROMPT)
        assert fp == _PINNED_PROCEDURAL_PROMPT_FP, (
            f"procedural memory _SYSTEM_PROMPT drifted: got {fp!r}, "
            f"expected {_PINNED_PROCEDURAL_PROMPT_FP!r}. Update the "
            "pin and add coverage for the new behaviour."
        )

    def test_system_prompt_includes_anti_injection_directive(self) -> None:
        """The prompt must include the untrusted-content directive.

        SynthOrg's prompt-safety contract requires every system prompt
        that consumes attacker-controllable text (task descriptions,
        error messages, tool outputs) to include an explicit
        untrusted-content directive. The proposer formats all three
        into the user message.
        """
        from synthorg.memory.procedural import proposer

        # Stable opening phrase rather than the full directive bytes
        # (those are produced by ``untrusted_content_directive`` and
        # may evolve as the prompt-safety contract tightens).
        assert "untrusted" in proposer._SYSTEM_PROMPT.lower(), (
            "_SYSTEM_PROMPT must include the untrusted-content "
            "directive (no 'untrusted' substring detected)"
        )

    def test_propose_call_site_uses_pinned_completion_config(self) -> None:
        """The propose() method must pass ``self._completion_config``.

        The construction-time check above proves the field is built
        correctly. This call-site check refuses any future refactor
        that constructs a fresh CompletionConfig inline.  AST walk
        instead of substring so a stray reference in a comment or
        docstring cannot satisfy the assertion.
        """
        from synthorg.memory.procedural.proposer import ProceduralMemoryProposer

        # ``inspect.getsource`` of a method preserves the class-body
        # indentation, which ``ast.parse`` rejects as an unexpected
        # indent.  Dedent before parsing.
        source = textwrap.dedent(inspect.getsource(ProceduralMemoryProposer.propose))
        tree = ast.parse(source)
        # Narrow to provider call sites only -- accept the obvious
        # ``provider``, ``self.provider``, and ``self._provider``
        # receivers.  Without this, an unrelated ``foo.complete(...)``
        # could satisfy the assertion even after a refactor that drops
        # ``self._completion_config`` from the actual provider call.
        provider_complete_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "complete"
            and ast.unparse(node.func.value)
            in {"provider", "self.provider", "self._provider"}
        ]
        assert provider_complete_calls, (
            "expected at least one provider.complete(...) call in "
            "ProceduralMemoryProposer.propose"
        )
        assert any(
            any(
                kw.arg == "config"
                and ast.unparse(kw.value) == "self._completion_config"
                for kw in call.keywords
            )
            for call in provider_complete_calls
        ), (
            "ProceduralMemoryProposer.propose must pass "
            "config=self._completion_config to provider.complete; a "
            "fresh inline CompletionConfig() construction would defeat "
            "the runtime-tunable contract"
        )
