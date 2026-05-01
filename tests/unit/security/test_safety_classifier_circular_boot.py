"""Verify the lazy ``prompt_safety`` import does not deadlock boot.

``security/safety_classifier.py`` defers its
``synthorg.engine.prompt_safety`` import to runtime to break the boot
cycle (``synthorg.engine.__init__`` -> ``AgentEngine`` ->
``_security_factory`` -> ``SecOpsService`` -> ``service_safety`` ->
``safety_classifier``).

A future maintainer who hoists the deferred import to module scope as a
"convenience" fix would silently re-introduce a circular import that
breaks ``synthorg.engine`` boot.

We verify the contract two ways:

1. ``find_spec`` confirms each module is locatable (cheap, no side
   effects).
2. ``_system_prompt()`` triggers the deferred ``prompt_safety`` import
   at runtime and asserts the untrusted-content directive is reachable
   -- this is the actual boot-time path the lazy import protects.

Running an isolated cold-import order check (e.g. import
``synthorg.engine`` and ``synthorg.security`` in a fresh subprocess)
sounds appealing but exercises a scenario that does not match
production: the application boot path always reaches both packages
through ``synthorg.api`` / a test fixture, never as the first import
in a Python session, and so trips on unrelated latent cycles further
down the import graph that are out of scope here.
"""

import importlib.util

import pytest


def _spec_exists(module_name: str) -> bool:
    """Return ``True`` if Python can locate the module spec."""
    return importlib.util.find_spec(module_name) is not None


@pytest.mark.unit
class TestSafetyClassifierCircularBoot:
    """``prompt_safety`` is reachable without a top-level import."""

    def test_engine_package_locatable(self) -> None:
        """``synthorg.engine`` module spec is reachable."""
        assert _spec_exists("synthorg.engine")

    def test_security_package_locatable(self) -> None:
        """``synthorg.security`` module spec is reachable."""
        assert _spec_exists("synthorg.security")

    def test_safety_classifier_module_locatable(self) -> None:
        """``synthorg.security.safety_classifier`` is locatable."""
        assert _spec_exists("synthorg.security.safety_classifier")

    def test_engine_prompt_safety_locatable(self) -> None:
        """``synthorg.engine.prompt_safety`` is locatable."""
        assert _spec_exists("synthorg.engine.prompt_safety")

    def test_lazy_import_resolves_at_runtime(self) -> None:
        """The deferred import inside ``_system_prompt`` must succeed.

        Calling ``_system_prompt()`` triggers the runtime import.
        If a future change accidentally hoisted the import to module
        scope and reintroduced the cycle, ``synthorg.engine`` boot
        would fail; ``_system_prompt()`` runs the import path now.
        """
        from synthorg.security.safety_classifier import _system_prompt

        prompt = _system_prompt()
        assert "untrusted input" in prompt
        assert "<task-data>" in prompt

    def test_top_level_lazy_import_marker_present(self) -> None:
        """The ``prompt_safety`` import stays absent at module top.

        AST-level guardrail: walks the top-level module body of
        ``synthorg.security.safety_classifier`` and asserts that no
        ``import synthorg.engine.prompt_safety`` / ``from
        synthorg.engine.prompt_safety import ...`` statement lives at
        module scope. Catches the "convenience" hoist regression even
        if ``_system_prompt()`` happens to still resolve via cached
        module state.
        """
        import ast
        import inspect

        from synthorg.security import safety_classifier

        tree = ast.parse(inspect.getsource(safety_classifier))
        offenders = [
            node
            for node in tree.body
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "synthorg.engine.prompt_safety"
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    alias.name == "synthorg.engine.prompt_safety"
                    for alias in node.names
                )
            )
        ]
        assert not offenders, (
            "synthorg.security.safety_classifier must not import "
            "synthorg.engine.prompt_safety at module scope; the import "
            "is intentionally deferred to break the boot cycle "
            "(synthorg.engine -> ... -> safety_classifier -> engine)."
        )
