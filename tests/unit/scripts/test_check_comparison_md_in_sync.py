"""Tests for scripts/check_comparison_md_in_sync.py.

Pins the gate's contract:

* committed Markdown matches the generator output -> exit 0
* a trailing-newline-only difference does not flap the gate -> exit 0
* committed Markdown drifts from the generator -> exit 1 with a diff
* the committed file is absent -> exit 1
* the generator raises (e.g. its private API was renamed, surfacing an
  ``AttributeError``) -> exit 1 with a clean diagnostic, never a traceback
"""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


def _import_script(name: str) -> ModuleType:
    """Load ``scripts/<name>.py`` as a module, mirroring the sibling tests."""
    script = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _import_script("check_comparison_md_in_sync")


def _fake_generator(markdown: str) -> SimpleNamespace:
    """A stand-in for generate_comparison exposing the two private callables."""
    return SimpleNamespace(
        _load_data=lambda: {"meta": {}},
        _generate_markdown=lambda _data: markdown,
    )


@pytest.mark.unit
def test_in_sync_returns_zero(tmp_path: Path) -> None:
    """Committed content equal to the generator output passes."""
    committed = tmp_path / "comparison.md"
    committed.write_text("EXPECTED", encoding="utf-8")
    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(check, "_load_generator", lambda: _fake_generator("EXPECTED")),
    ):
        assert check.main() == 0


@pytest.mark.unit
def test_trailing_newline_only_diff_is_clean(tmp_path: Path) -> None:
    """A committed trailing newline (generator joins without one) still passes."""
    committed = tmp_path / "comparison.md"
    committed.write_text("EXPECTED\n", encoding="utf-8")
    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(check, "_load_generator", lambda: _fake_generator("EXPECTED")),
    ):
        assert check.main() == 0


@pytest.mark.unit
def test_drift_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Committed content differing from the generator fails with a diff."""
    committed = tmp_path / "comparison.md"
    committed.write_text("STALE", encoding="utf-8")
    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(check, "_load_generator", lambda: _fake_generator("FRESH")),
    ):
        assert check.main() == 1
    err = capsys.readouterr().err
    assert "out of sync" in err
    assert check._REMEDIATION in err


@pytest.mark.unit
def test_missing_output_file_returns_one(tmp_path: Path) -> None:
    """An absent committed file fails cleanly."""
    with patch.object(check, "_OUTPUT_FILE", tmp_path / "absent.md"):
        assert check.main() == 1


@pytest.mark.unit
def test_generator_error_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A generator API rename (AttributeError) yields exit 1, not a traceback."""
    committed = tmp_path / "comparison.md"
    committed.write_text("EXPECTED", encoding="utf-8")

    def _broken_loader() -> ModuleType:
        msg = "generate_comparison has no attribute '_load_data'"
        raise AttributeError(msg)

    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(check, "_load_generator", _broken_loader),
    ):
        assert check.main() == 1
    err = capsys.readouterr().err
    assert "could not generate expected comparison page" in err
    # The whole point is a friendly one-liner, NOT a crash dump: assert the
    # multi-line Python traceback never leaks to stderr so a future regression
    # that drops the handler is caught here. The handler intentionally names the
    # exception type in its one-liner ("...: AttributeError: ..."), so only the
    # traceback header (which a real stack dump always carries) is asserted absent.
    assert "Traceback (most recent call last)" not in err
