"""Tests for scripts/check_docs_nav_coverage.py.

The gate walks the ``nav:`` tree in ``mkdocs.yml``, globs
``docs/**/*.md``, and fails when an on-disk page is neither in the nav
nor on the documented internal allowlist, or when a nav entry points at
a file that no longer exists.

These tests build a synthetic ``mkdocs.yml`` + ``docs/`` tree under
``tmp_path`` and patch the module's path constants + allowlist so they
never depend on the real repository state.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def _import_script() -> ModuleType:
    """Import scripts/check_docs_nav_coverage.py as a module."""
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "check_docs_nav_coverage.py"
    )
    spec = importlib.util.spec_from_file_location("check_docs_nav_coverage", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _import_script()


def _build_docs(tmp_path: Path, pages: tuple[str, ...]) -> tuple[Path, Path]:
    """Create a docs/ tree with *pages* and return (docs_dir, mkdocs_path)."""
    docs_dir = tmp_path / "docs"
    for rel in pages:
        target = docs_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# stub\n", encoding="utf-8")
    return docs_dir, tmp_path / "mkdocs.yml"


def _write_nav(mkdocs_path: Path, nav_yaml: str) -> None:
    """Write a minimal mkdocs.yml carrying *nav_yaml*."""
    mkdocs_path.write_text(f"site_name: Test\n{nav_yaml}", encoding="utf-8")


def _run(
    gate_mod: ModuleType,
    docs_dir: Path,
    mkdocs_path: Path,
    allowlist: dict[str, str],
) -> int:
    """Invoke main() with the module constants patched to the tmp tree."""
    with (
        patch.object(gate_mod, "DOCS_DIR", docs_dir),
        patch.object(gate_mod, "MKDOCS_FILE", mkdocs_path),
        patch.object(gate_mod, "ALLOWLIST", allowlist),
    ):
        exit_code: int = gate_mod.main()
        return exit_code


def test_collect_nav_md_ignores_none_and_collects_md() -> None:
    out: set[str] = set()
    # ``None`` (from !ENV / !!python/name tag resolution) is skipped.
    gate._collect_nav_md(None, out)
    gate._collect_nav_md(
        [{"Home": "index.md"}, {"Guides": [{"A": "guides/a.md"}, None]}], out
    )
    assert out == {"index.md", "guides/a.md"}


def test_clean_tree_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md", "guides/a.md"))
    _write_nav(mkdocs, "nav:\n  - Home: index.md\n  - Guides:\n      - guides/a.md\n")
    assert _run(gate, docs_dir, mkdocs, {}) == 0
    assert "all reachable from nav" in capsys.readouterr().out


def test_page_not_in_nav_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md", "orphan.md"))
    _write_nav(mkdocs, "nav:\n  - Home: index.md\n")
    assert _run(gate, docs_dir, mkdocs, {}) == 1
    assert "orphan.md" in capsys.readouterr().err


def test_allowlisted_page_passes(tmp_path: Path) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md", "internal.md"))
    _write_nav(mkdocs, "nav:\n  - Home: index.md\n")
    assert _run(gate, docs_dir, mkdocs, {"internal.md": "internal dev note"}) == 0


def test_nav_entry_missing_on_disk_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md",))
    _write_nav(mkdocs, "nav:\n  - Home: index.md\n  - Gone: gone.md\n")
    assert _run(gate, docs_dir, mkdocs, {}) == 1
    assert "gone.md" in capsys.readouterr().err


def test_stale_allowlist_entry_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md",))
    _write_nav(mkdocs, "nav:\n  - Home: index.md\n")
    assert _run(gate, docs_dir, mkdocs, {"vanished.md": "no longer here"}) == 1
    assert "vanished.md" in capsys.readouterr().err


def test_allowlist_entry_also_in_nav_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md", "dual.md"))
    _write_nav(mkdocs, "nav:\n  - Home: index.md\n  - Dual: dual.md\n")
    assert _run(gate, docs_dir, mkdocs, {"dual.md": "should not be allowlisted"}) == 1
    assert "dual.md" in capsys.readouterr().err


def test_missing_nav_mapping_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md",))
    mkdocs.write_text("site_name: Test\n", encoding="utf-8")
    assert _run(gate, docs_dir, mkdocs, {}) == 1
    assert "error:" in capsys.readouterr().err


def test_malformed_yaml_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir, mkdocs = _build_docs(tmp_path, ("index.md",))
    mkdocs.write_text("nav: [unterminated\n", encoding="utf-8")
    assert _run(gate, docs_dir, mkdocs, {}) == 1
    assert "error:" in capsys.readouterr().err
