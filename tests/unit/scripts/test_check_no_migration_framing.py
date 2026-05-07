"""Unit tests for ``scripts/check_no_migration_framing.py``.

Loads the script as a module so its private helpers are callable
without spawning subprocesses.

Covers:

* Positive matches for every forbidden pattern (``ported from``,
  ``previously called``, ``renamed from``, ``Phase \\d+``,
  ``we used to``, ``moved here in``, ``Round-\\d+ (fix|review)``).
* Negative cases for look-alikes (``re-exported from``,
  ``CoordinationPhaseResult`` -- bare ``Phase`` without digit,
  ``the phases of the moon``).
* Per-line opt-out (``# lint-allow: migration-framing -- <reason>``).
* Path allowlist + scope.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_migration_framing.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_no_migration_framing",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _write_fixture(tmp_path: Path, relative: str, content: str) -> Path:
    """Write a fake repo file under tmp_path and return its path."""
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _scan(tmp_path: Path, relative: str, content: str) -> list[str]:
    """Invoke ``_scan_file`` against a tmp-path fixture."""
    fp = _write_fixture(tmp_path, relative, content)
    rel = fp.relative_to(tmp_path).as_posix()
    issues: list[str] = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
    return issues


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    """Yield the tmp-path root; tests write fixtures under it."""
    return tmp_path


class TestPortedFrom:
    """``ported from`` is migration framing."""

    def test_ported_from_lowercase(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""ported from foo.bar in v0.6."""\n',
        )
        assert any("ported from" in i.lower() for i in issues), issues

    def test_re_exported_from_not_flagged(self, src_dir: Path) -> None:
        """``re-exported from`` is current-state, not framing."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""Public surface (re-exported from ws.py)."""\n',
        )
        assert issues == [], issues

    def test_imported_from_not_flagged(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Helpers imported from utils.py\n",
        )
        assert issues == [], issues


class TestPreviouslyCalled:
    """``previously called`` is migration framing."""

    def test_previously_called(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""TrainingController previously called repo.save() directly."""\n',
        )
        assert any("previously called" in i.lower() for i in issues), issues


class TestRenamedFrom:
    """``renamed from`` is migration framing."""

    def test_renamed_from(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""Class renamed from FooBar to FooBaz."""\n',
        )
        assert any("renamed from" in i.lower() for i in issues), issues


class TestPhaseN:
    """``Phase \\d+`` (any case) is migration framing."""

    def test_phase_capital(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Phase 1: Decompose the task\n",
        )
        assert any("phase" in i.lower() for i in issues), issues

    def test_phase_lowercase(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "description = 'set after phase 1'\n",
        )
        assert any("phase" in i.lower() for i in issues), issues

    def test_phase_no_digit_not_flagged(self, src_dir: Path) -> None:
        """Bare ``Phase`` without digit (domain class names) is allowed."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "from synthorg import CoordinationPhaseResult\n",
        )
        assert issues == [], issues

    def test_phases_plural_not_flagged(self, src_dir: Path) -> None:
        """``phases`` plural without immediate digit is allowed."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# the pipeline has multiple phases of execution\n",
        )
        assert issues == [], issues

    def test_phase_with_decimal_flagged(self, src_dir: Path) -> None:
        """Even ``Phase 1.5`` (decimal) gets flagged on the integer prefix."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Phase 1.5 -- D26\n",
        )
        assert any("phase" in i.lower() for i in issues), issues


class TestWeUsedTo:
    """``we used to`` is migration framing."""

    def test_we_used_to(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""we used to dispatch from this module."""\n',
        )
        assert any("we used to" in i.lower() for i in issues), issues


class TestRoundNFix:
    """``Round-\\d+ fix`` / ``round-\\d+ review`` is framing."""

    def test_round_dash_n_fix(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "tests/unit/x.py",
            '"""Round-3 fix: subscription rotation rejects tos_accepted=false."""\n',
        )
        assert any("round" in i.lower() for i in issues), issues

    def test_round_dash_n_review(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# round-12 review surfaced this\n",
        )
        assert any("round" in i.lower() for i in issues), issues


class TestSuppressionMarker:
    """``# lint-allow: migration-framing -- <reason>`` suppresses with reason."""

    def test_marker_with_reason_same_line(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'PATTERN = "Phase 1"  # lint-allow: migration-framing -- gate fixture\n',
        )
        assert issues == [], issues

    def test_marker_with_reason_preceding_line(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            (
                "# lint-allow: migration-framing -- documenting the rule\n"
                "# Phase 1: Decompose the task\n"
            ),
        )
        assert issues == [], issues

    def test_marker_without_reason_does_not_suppress(self, src_dir: Path) -> None:
        """Empty justification must NOT suppress."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'PATTERN = "Phase 1"  # lint-allow: migration-framing\n',
        )
        assert any("phase" in i.lower() for i in issues), issues


class TestPathAllowlist:
    """Files under canonical-doc paths are not scanned."""

    def test_docs_design_skipped(self, src_dir: Path) -> None:
        fp = _write_fixture(
            src_dir,
            "docs/design/coordination.md",
            "# Phase 1: Decompose. The protocol previously called bar.\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_docs_reference_skipped(self, src_dir: Path) -> None:
        fp = _write_fixture(
            src_dir,
            "docs/reference/conventions.md",
            "Forbidden: ported from, previously called, Phase 1.\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_changelog_skipped(self, src_dir: Path) -> None:
        fp = _write_fixture(
            src_dir,
            "CHANGELOG.md",
            "* Phase 1 done; ported from old module.\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_scanner_self_test_skipped(self, src_dir: Path) -> None:
        """The gate's own test file may contain forbidden tokens."""
        fp = _write_fixture(
            src_dir,
            "tests/unit/scripts/test_check_no_migration_framing.py",
            'fixture = "ported from foo, Phase 1, previously called bar"\n',
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_scanner_self_skipped(self, src_dir: Path) -> None:
        fp = _write_fixture(
            src_dir,
            "scripts/check_no_migration_framing.py",
            'PATTERN = "Phase \\\\d+"\n',
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues


class TestScopeLimit:
    """Only ``*.py`` / ``*.sql`` files under in-scope roots are scanned."""

    def test_outside_scope_ignored(self, src_dir: Path) -> None:
        fp = _write_fixture(src_dir, "random/x.py", "# Phase 1: Decompose\n")
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_non_python_skipped(self, src_dir: Path) -> None:
        """``.txt`` under src/synthorg/ is not scanned."""
        fp = _write_fixture(
            src_dir,
            "src/synthorg/notes.txt",
            "# Phase 1: Decompose\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues


class TestPathTraversal:
    """``--paths ../..`` is rejected via the project-root anchor."""

    def test_resolve_root_outside(self, tmp_path: Path) -> None:
        outside = Path("..") / ".." / "etc"
        resolved = _MODULE._resolve_root(outside, tmp_path)  # type: ignore[attr-defined]
        assert resolved is None
