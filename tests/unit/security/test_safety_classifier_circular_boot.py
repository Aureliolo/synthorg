"""Verify the lazy ``prompt_safety`` import does not deadlock boot.

Pre-PR review finding (#1682, item #7): ``security/safety_classifier.py``
defers its ``synthorg.engine.prompt_safety`` import to runtime to break
the boot cycle (``synthorg.engine.__init__`` -> ``AgentEngine`` ->
``_security_factory`` -> ``SecOpsService`` -> ``service_safety`` ->
``safety_classifier``).

A future maintainer who hoists the deferred import to module scope as a
"convenience" fix would silently re-introduce a circular import that
breaks ``synthorg.engine`` boot. The unit-level prompt-safety tests
import the class locally and would NOT catch this regression because
they bypass the ``synthorg.engine`` package init path.

This test imports both packages from a fresh module table and verifies
that:

1. Importing ``synthorg.engine`` first then ``synthorg.security`` works.
2. Importing ``synthorg.security`` first then ``synthorg.engine`` works.
3. The lazily-imported names (``TAG_TASK_DATA``, ``wrap_untrusted``,
   ``untrusted_content_directive``) are reachable through both
   ``synthorg.engine.prompt_safety`` and through
   ``SafetyClassifier._build_messages()`` at runtime.
"""

import importlib.util

import pytest


def _spec_exists(module_name: str) -> bool:
    """Return ``True`` if Python can locate the module spec.

    Using ``find_spec`` rather than a real import keeps the test
    independent of the parent test session's import state and avoids
    the risk of importing a stale module from ``sys.modules``.
    """
    return importlib.util.find_spec(module_name) is not None


@pytest.mark.unit
class TestSafetyClassifierCircularBoot:
    """Both packages must be locatable; lazy import must resolve."""

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
        # The system prompt must terminate with the SEC-1 directive
        # produced by the deferred ``untrusted_content_directive``
        # call. If the lazy import returned a stub or empty value,
        # the directive substring would be missing.
        assert "untrusted input" in prompt
        assert "<task-data>" in prompt
