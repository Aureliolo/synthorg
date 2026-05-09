"""Prompt eval: supervisor router temperature + prompt drift.

Asserts the deterministic properties of the hierarchical retrieval
supervisor router:

1. ``_ROUTING_COMPLETION_CONFIG.temperature`` is pinned to ``0.0`` so
   routing decisions are reproducible across CI shards.
2. The bytes of ``_ROUTING_SYSTEM_PROMPT`` and ``_RETRY_SYSTEM_PROMPT``
   haven't silently drifted: intentional prompt edits must update the
   pinned fingerprints in this file.
3. The two production call sites pass the pinned config to
   ``provider.complete``; a future refactor that constructs a fresh
   config inline fails the call-site test.
"""

import inspect

import pytest

from tests.evals.prompt._harness import fingerprint_prompt

# Pinned SHA-256[:16] hashes of the routing + retry prompt bodies. Update
# these when the prompts change intentionally; pair the bump with new
# labelled-example coverage so the regression net stays meaningful.
_PINNED_ROUTING_PROMPT_FP = "8a89b74f71c5db10"
_PINNED_RETRY_PROMPT_FP = "f00d41e5150e98c8"


@pytest.mark.unit
class TestSupervisorRouterPromptContract:
    """Guard rails for the supervisor router prompt surface."""

    def test_temperature_is_zero(self) -> None:
        """Routing + retry must run at temperature=0 for determinism."""
        from synthorg.memory.retrieval.hierarchical import supervisor

        runtime_config = supervisor._ROUTING_COMPLETION_CONFIG
        assert runtime_config.temperature == 0.0, (
            "_ROUTING_COMPLETION_CONFIG.temperature must equal 0.0 at "
            f"runtime; got {runtime_config.temperature!r}"
        )

    def test_routing_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift in ``_ROUTING_SYSTEM_PROMPT``."""
        from synthorg.memory.retrieval.hierarchical import supervisor

        fp = fingerprint_prompt(supervisor._ROUTING_SYSTEM_PROMPT)
        assert fp == _PINNED_ROUTING_PROMPT_FP, (
            f"supervisor router routing prompt drifted: got {fp!r}, "
            f"expected {_PINNED_ROUTING_PROMPT_FP!r}. Update the pin "
            "and add coverage for the new behaviour."
        )

    def test_retry_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift in ``_RETRY_SYSTEM_PROMPT``."""
        from synthorg.memory.retrieval.hierarchical import supervisor

        fp = fingerprint_prompt(supervisor._RETRY_SYSTEM_PROMPT)
        assert fp == _PINNED_RETRY_PROMPT_FP, (
            f"supervisor router retry prompt drifted: got {fp!r}, "
            f"expected {_PINNED_RETRY_PROMPT_FP!r}. Update the pin "
            "and add coverage for the new behaviour."
        )

    def test_call_sites_use_pinned_config(self) -> None:
        """Both LLM call sites must pass the module-level pinned config.

        AST-level check is intentionally simple (``config=`` keyword
        bound to the constant name) because the surface is small and
        any cleverer refactor that constructs a fresh CompletionConfig
        inline should be caught by ``test_temperature_is_zero``
        regressing or this assertion failing.
        """
        from synthorg.memory.retrieval.hierarchical import supervisor

        source = inspect.getsource(supervisor)
        # Two distinct call sites: ``_route_via_llm`` and
        # ``_evaluate_via_llm``.  Each must pass the pinned config.
        occurrences = source.count("config=_ROUTING_COMPLETION_CONFIG")
        assert occurrences >= 2, (
            "expected both supervisor.complete() call sites to pass "
            f"config=_ROUTING_COMPLETION_CONFIG; found {occurrences}"
        )
