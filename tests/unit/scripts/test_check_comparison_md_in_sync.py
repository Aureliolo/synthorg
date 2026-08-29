"""Tests for scripts/check_comparison_md_in_sync.py.

Pins the gate's contract:

* committed Markdown matches the generator output -> exit 0
* a trailing-newline-only difference does not flap the gate -> exit 0
* committed Markdown drifts from the generator -> exit 1 with a diff
* the committed file is absent -> exit 1
* the generator raises (e.g. its private API was renamed, surfacing an
  ``AttributeError``) -> exit 1 with a clean diagnostic, never a traceback
* the git-derived date line differs but the content does not, under the
  ``auto`` sentinel -> exit 0
* the same date difference under a pinned date -> exit 1
* a YAML whose ``meta`` block is not a mapping -> exit 1 with a clean
  diagnostic, because the typed parse rejects it before any field is read
"""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

_AUTO: str = "auto"


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


def _fake_generator(
    markdown: str,
    *,
    data_file: Path | None = None,
    declared: str = _AUTO,
) -> SimpleNamespace:
    """A stand-in for generate_comparison exposing what the gate reads.

    The gate reads ``DATA_FILE`` and ``AUTO_SENTINEL`` as well as the two
    private callables, because whether the rendered date is git-derived
    decides whether that line is compared. A fake missing either would send
    the gate down its error path and pass a test for the wrong reason.

    Args:
        markdown: What the fake generator renders.
        data_file: Where the fake writes its YAML; omitted when the test does
            not exercise the sentinel.
        declared: The ``meta.last_updated`` value the YAML declares.

    Returns:
        The stand-in module.
    """
    if data_file is not None:
        data_file.write_text(f'meta:\n  last_updated: "{declared}"\n', encoding="utf-8")
    return SimpleNamespace(
        _load_data=lambda: {"meta": {}},
        _generate_markdown=lambda _data: markdown,
        DATA_FILE=data_file if data_file is not None else Path("unused.yaml"),
        AUTO_SENTINEL=_AUTO,
    )


@pytest.mark.unit
def test_in_sync_returns_zero(tmp_path: Path) -> None:
    """Committed content equal to the generator output passes."""
    committed = tmp_path / "comparison.md"
    committed.write_text("EXPECTED", encoding="utf-8")
    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(
            check,
            "_load_generator",
            lambda: _fake_generator("EXPECTED", data_file=tmp_path / "data.yaml"),
        ),
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
        patch.object(
            check,
            "_load_generator",
            lambda: _fake_generator("EXPECTED", data_file=tmp_path / "data.yaml"),
        ),
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
        patch.object(
            check,
            "_load_generator",
            lambda: _fake_generator("FRESH", data_file=tmp_path / "data.yaml"),
        ),
    ):
        assert check.main() == 1
    err = capsys.readouterr().err
    assert "out of sync" in err
    assert check._REMEDIATION in err


@pytest.mark.unit
def test_derived_date_difference_alone_is_not_drift(tmp_path: Path) -> None:
    """A git-derived date the merge rewrote is not content drift.

    The generator renders the committer date of the last commit touching the
    YAML. A squash-merge mints a new commit with a new date, after the last
    point anyone could regenerate, so this difference is the merge and not the
    page. It left main red on a page whose content was correct.
    """
    committed = tmp_path / "comparison.md"
    committed.write_text(
        "Comparison data last changed: 2026-08-28\n\nBODY", encoding="utf-8"
    )
    generated = "Comparison data last changed: 2026-08-29\n\nBODY"
    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(
            check,
            "_load_generator",
            lambda: _fake_generator(generated, data_file=tmp_path / "data.yaml"),
        ),
    ):
        assert check.main() == 0


@pytest.mark.unit
def test_pinned_date_difference_is_still_drift(tmp_path: Path) -> None:
    """A pinned date is authored content, so a difference there still fails.

    Only the ``auto`` sentinel makes the date git-derived. A pinned value comes
    from the YAML and no merge can change it, so drift means somebody edited
    one side without regenerating.
    """
    committed = tmp_path / "comparison.md"
    committed.write_text(
        "Comparison data last changed: 2026-08-28\n\nBODY", encoding="utf-8"
    )
    generated = "Comparison data last changed: 2026-08-29\n\nBODY"
    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(
            check,
            "_load_generator",
            lambda: _fake_generator(
                generated, data_file=tmp_path / "data.yaml", declared="2026-08-29"
            ),
        ),
    ):
        assert check.main() == 1


@pytest.mark.unit
def test_malformed_meta_block_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``meta`` block that is not a mapping fails cleanly, not with a crash.

    Reading ``meta.last_updated`` off the raw mapping raised ``AttributeError``
    from inside the gate. The typed parse refuses the shape first, so the
    operator gets the gate's own diagnostic and an exit code.
    """
    committed = tmp_path / "comparison.md"
    committed.write_text("EXPECTED", encoding="utf-8")
    data_file = tmp_path / "data.yaml"

    def _generator() -> SimpleNamespace:
        data_file.write_text("meta: not-a-mapping\n", encoding="utf-8")
        return SimpleNamespace(
            _load_data=lambda: {"meta": {}},
            _generate_markdown=lambda _data: "EXPECTED",
            DATA_FILE=data_file,
            AUTO_SENTINEL=_AUTO,
        )

    with (
        patch.object(check, "REPO_ROOT", tmp_path),
        patch.object(check, "_OUTPUT_FILE", committed),
        patch.object(check, "_load_generator", _generator),
    ):
        assert check.main() == 1
    assert "could not generate expected comparison page" in capsys.readouterr().err


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
