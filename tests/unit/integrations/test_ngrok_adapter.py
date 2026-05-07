"""Tests for the ngrok tunnel adapter (regression guards)."""

import pytest


@pytest.mark.unit
class TestPyngrokIsRequiredDependency:
    """``pyngrok`` is a required runtime dep, not optional.

    A try/except-ImportError guard would silently degrade the tunnel
    feature to 503 at runtime when the dep is missing.  ``pyngrok``
    is declared in ``[project.dependencies]`` and the adapter imports
    it unconditionally; a missing dep is a build / install bug that
    surfaces at module load. These tests are smoke-
    grade regression guards: if ``pyngrok`` is ever removed from
    ``pyproject.toml`` the tests fail at import time, well before any
    runtime tunnel request.
    """

    def test_pyngrok_module_imports_cleanly(self) -> None:
        """``import pyngrok`` succeeds (i.e., it is installed)."""
        import importlib

        # Use importlib.import_module so mypy doesn't try to resolve
        # the (untyped) pyngrok module's attributes at type-check time.
        pyngrok = importlib.import_module("pyngrok")
        assert pyngrok is not None

    def test_ngrok_adapter_imports_pyngrok_unconditionally(self) -> None:
        """The adapter module imports ``pyngrok`` at module load.

        If a future refactor reintroduces the ``try/except ImportError``
        guard, this assertion still passes (the import would still
        succeed in this environment), but the companion source-grep
        regression test below catches that case.
        """
        from synthorg.integrations.tunnel import ngrok_adapter

        # The module-level bindings `conf` and `ngrok` must be live --
        # not None placeholders left by a guarded import. Access via
        # ``getattr`` so mypy's strict ``--no-implicit-reexport`` does
        # not flag re-exposed third-party attributes.
        assert getattr(ngrok_adapter, "conf", None) is not None
        assert getattr(ngrok_adapter, "ngrok", None) is not None

    def test_no_import_guard_in_adapter_source(self) -> None:
        """Source-level guard: no ``try/except ImportError`` wraps pyngrok.

        A failing import is a build / install bug per the design
        contract, not a runtime configuration concern. This grep-level
        assertion fails loudly if the guard ever creeps back in.
        """
        from pathlib import Path

        import synthorg.integrations.tunnel.ngrok_adapter as adapter_module

        source_path = Path(adapter_module.__file__)
        source = source_path.read_text(encoding="utf-8")
        # Look for the specific guarded form. A bare ``import pyngrok``
        # inside an ``except ImportError`` arm in some unrelated test
        # helper would not be flagged, only the production module's
        # import.
        assert "from pyngrok import" in source
        # Crude but effective: the guard pattern is a single-line
        # ``try:`` followed by the pyngrok import within ~5 lines.
        for idx, line in enumerate(source.splitlines()):
            if "from pyngrok import" not in line:
                continue
            # Check the previous handful of lines for a ``try:`` that
            # could indicate a guarded import.
            window = source.splitlines()[max(0, idx - 6) : idx]
            assert not any(line_text.strip() == "try:" for line_text in window), (
                "pyngrok import appears to be guarded by try/except; "
                "the dep is required, the import must be unconditional."
            )
