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
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    """Load the warmer by path.

    Returns:
        The module. ``ModuleType.__getattr__`` is already typed ``Any``,
        so attribute access resolves without an explicit-Any opt-out.
    """
    script = _REPO_ROOT / "scripts" / "warm_typeguard_cache.py"
    spec = importlib.util.spec_from_file_location("_warm_typeguard_cache", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


class TestFailureMarker:
    """The detached warm's exit code goes nowhere, so it leaves a marker."""

    @pytest.fixture
    def hooks(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        monkeypatch.setattr(_MODULE, "hooks_dir", lambda: tmp_path)
        name: str = _MODULE.WARM_FAILED_MARKER
        return tmp_path / name

    def test_a_failed_warm_leaves_one(
        self, hooks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3, []))
        assert _MODULE.main(["--quiet", "--mark-failures"]) == 1
        assert hooks.is_file()

    def test_a_later_success_retires_it(
        self, hooks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Left behind, it warns about a cache that is now warm, and a
        # warning that fires when nothing is wrong stops being read.
        hooks.write_text("stale", encoding="utf-8")
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3710, []))
        assert _MODULE.main(["--quiet", "--mark-failures"]) == 0
        assert not hooks.exists()

    def test_an_interactive_run_leaves_no_marker(
        self, hooks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without the flag the caller sees the exit code directly, so a
        # marker would only surface a failure they already handled.
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3, []))
        assert _MODULE.main(["--quiet"]) == 1
        assert not hooks.exists()

    def test_an_unknown_hooks_directory_is_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_MODULE, "hooks_dir", lambda: None)
        monkeypatch.setattr(_MODULE, "_walk_package", lambda *, quiet: (3, []))
        assert _MODULE.main(["--quiet", "--mark-failures"]) == 1
