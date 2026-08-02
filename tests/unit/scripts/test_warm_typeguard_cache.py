"""Tests for the typeguard bytecode-cache warmer.

The warmer's whole value is negative: nothing observable changes when it
works, and everything keeps working (just 17s per process slower) when it
silently does not. So the behaviour worth pinning is that it refuses to
report success without having covered the package, and that it cannot be
run in a mode where caching is impossible.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> Any:  # type: ignore[explicit-any]
    script = _REPO_ROOT / "scripts" / "warm_typeguard_cache.py"
    spec = importlib.util.spec_from_file_location("_warm_typeguard_cache", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)  # type: ignore[explicit-any]


_MODULE = _load()


@pytest.fixture(autouse=True)
def _no_real_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the warm off the real package; it costs ~15s and writes .pyc."""
    monkeypatch.setattr(_MODULE, "install_import_hook", lambda _packages: None)
    monkeypatch.setattr(_MODULE.importlib, "import_module", lambda _name: None)


class TestCoverageGuard:
    """A warm that reached almost nothing must not report success."""

    def test_a_full_walk_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3710, []))
        assert _MODULE.main(["--quiet"]) == 0

    def test_a_walk_that_covered_nothing_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Exiting 0 here is the silent regression: every worker keeps paying
        # full price while the warm step reports itself green.
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3, []))
        assert _MODULE.main(["--quiet"]) == 1

    def test_unimportable_modules_do_not_fail_the_warm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An absent optional extra is not a reason to fail a cache warm.
        monkeypatch.setattr(
            _MODULE,
            "_walk_package",
            lambda *, quiet: (3710, ["synthorg.x: ImportError: no torch"]),
        )
        assert _MODULE.main(["--quiet"]) == 0


class TestBytecodeWritingIsRequired:
    """Warming with bytecode writing off would cache nothing at all."""

    def test_refuses_when_bytecode_writing_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3710, []))
        assert _MODULE.main(["--quiet"]) == 1
