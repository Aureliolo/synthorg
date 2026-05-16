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
* Per-line opt-out (``# lint-allow: migration-framing -- <reason>``)
  with both ``--`` and ``:`` separators, marker-in-string-literal
  isolation, whitespace-only reason rejection.
* Path allowlist + scope.
* Error-path fall-backs (tokenize error, file-read error).
* Direct unit tests for marker helpers.
* I/O-error sentinel prefix routing.
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


class TestMovedHereIn:
    """``moved here in`` is migration framing."""

    def test_moved_here_in_lowercase(self, src_dir: Path) -> None:
        """``moved here in <version>`` flags as migration framing."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""Service moved here in v0.7."""\n',
        )
        assert any("moved here in" in i.lower() for i in issues), issues

    def test_moved_here_in_phase_n(self, src_dir: Path) -> None:
        """The canonical example from the docstring fires both gates."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# moved here in Phase 2 of the refactor\n",
        )
        # Both ``moved here in`` and ``Phase 2`` patterns should match
        # (the line carries both).
        assert any("moved here in" in i.lower() for i in issues), issues
        assert any("phase" in i.lower() for i in issues), issues

    def test_moved_alone_not_flagged(self, src_dir: Path) -> None:
        """The bare verb ``moved`` (without ``here in``) is fine."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# values are moved between buckets each step\n",
        )
        assert issues == [], issues


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
        """``Phase 1.5`` is flagged because the regex matches the integer ``1``.

        The pattern ``\\bphase\\s+\\d+`` consumes ``Phase 1`` and stops
        before ``.5`` because ``\\d+`` does not span the decimal point.
        Flagging is what we want -- decimal phases are still phases --
        but the test name documents what the regex actually does.
        """
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

    def test_marker_whitespace_reason_does_not_suppress(self, src_dir: Path) -> None:
        """Whitespace-only justification after ``--`` must NOT suppress."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'PATTERN = "Phase 1"  # lint-allow: migration-framing --   \n',
        )
        assert any("phase" in i.lower() for i in issues), issues

    def test_marker_with_colon_separator(self, src_dir: Path) -> None:
        """The marker accepts ``:`` as separator (not just ``--``)."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'PATTERN = "Phase 1"  # lint-allow: migration-framing: gate fixture\n',
        )
        assert issues == [], issues

    def test_marker_with_colon_separator_empty_reason(self, src_dir: Path) -> None:
        """``migration-framing:`` with empty reason must NOT suppress."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'PATTERN = "Phase 1"  # lint-allow: migration-framing:   \n',
        )
        assert any("phase" in i.lower() for i in issues), issues

    def test_marker_inside_string_literal_does_not_suppress(
        self, src_dir: Path
    ) -> None:
        """Marker inside a string literal must NOT suppress on the same line."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'x = "# lint-allow: migration-framing -- ok"; y = "Phase 1"\n',
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

    @pytest.mark.parametrize(
        "rel_path",
        [
            "_audit/findings/x.py",
            ".claude/agents/x.py",
            ".github/scripts/x.py",
            "src/synthorg/persistence/postgres/revisions/0001_init.sql",
            "src/synthorg/persistence/sqlite/revisions/0001_init.sql",
        ],
    )
    def test_allowlisted_path_prefixes_skipped(
        self, src_dir: Path, rel_path: str
    ) -> None:
        """Each allowlist prefix is honoured: gate scans nothing under it."""
        fp = _write_fixture(
            src_dir,
            rel_path,
            "# Phase 1: ported from previously called bar -- we used to do X\n",
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

    def test_in_scope_sql_flagged(self, src_dir: Path) -> None:
        """SQL under ``src/synthorg/`` is scanned, not skipped."""
        issues = _scan(
            src_dir,
            "src/synthorg/persistence/schema.sql",
            "-- ported from old schema\n",
        )
        assert any("ported from" in i.lower() for i in issues), issues

    def test_in_scope_sql_trailing_marker_suppresses(self, src_dir: Path) -> None:
        """SQL trailing marker suppresses the violation."""
        issues = _scan(
            src_dir,
            "src/synthorg/persistence/schema.sql",
            "SELECT 1; -- Phase 1 -- lint-allow: migration-framing -- gate fixture\n",
        )
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


class TestPatternParametrized:
    """Every forbidden framing pattern fires on a representative fixture."""

    @pytest.mark.parametrize(
        ("label", "fixture"),
        [
            ("ported from", '"""ported from foo.bar in v0.6."""\n'),
            (
                "previously called",
                '"""TrainingController previously called repo.save()."""\n',
            ),
            ("renamed from", '"""Class renamed from FooBar to FooBaz."""\n'),
            ("moved here in", '"""Service moved here in v0.7."""\n'),
            ("we used to", '"""we used to dispatch from this module."""\n'),
            ("Phase N", "# Phase 4: typed-args refactor\n"),
            ("Round-N", '"""Round-7 review surfaced this."""\n'),
            (
                "previously lived",
                '"""Loop that previously lived in every mutation method."""\n',
            ),
            (
                "previously inlined",
                '"""Helpers that were previously inlined in app.py."""\n',
            ),
            (
                "previously duplicated",
                '"""Validation that was previously duplicated across files."""\n',
            ),
            (
                "previously scattered",
                '"""Used to be scattered across 15 handlers."""\n',
            ),
            (
                "were previously owned",
                '"""Helpers that were previously owned by the bus module."""\n',
            ),
            (
                "were previously emitted",
                '"""Events that were previously emitted by the legacy hook."""\n',
            ),
            (
                "were previously wrapped",
                '"""Calls that were previously wrapped in retry shims."""\n',
            ),
            (
                "used to be",
                '"""These checks used to be tolerated in per-agent fan-out."""\n',
            ),
            (
                "originally generated",
                '"""proposed_at: When the proposal was originally generated."""\n',
            ),
            (
                "originally promised",
                '"""length matches the bytes the inner app originally promised."""\n',
            ),
            (
                "originally-claimed",
                '"""Passes the originally-claimed record to unregister."""\n',
            ),
        ],
    )
    def test_pattern_fires(self, src_dir: Path, label: str, fixture: str) -> None:
        """A representative line for *label* is flagged by the gate."""
        issues = _scan(src_dir, "src/synthorg/x.py", fixture)
        assert issues, (label, issues)

    @pytest.mark.parametrize(
        ("label", "fixture"),
        [
            (
                "runtime previously stored",
                '"""Rotating the key orphans all previously stored ciphertext."""\n',
            ),
            (
                "runtime previously applied",
                '"""Reject a row whose content changed since previously applied."""\n',
            ),
            (
                "runtime previously compacted",
                '"""Reject a conversation that was previously compacted upstream."""\n',
            ),
            (
                "runtime previously completed",
                '"""If the task was previously completed, skip the re-run."""\n',
            ),
        ],
    )
    def test_runtime_state_descriptions_not_flagged(
        self,
        src_dir: Path,
        label: str,
        fixture: str,
    ) -> None:
        """Runtime-state descriptions are not migration framing.

        Bare ``previously`` plus a runtime verb (``stored ciphertext``,
        ``compacted conversation``) describes program state, not a
        code-move. The targeted verb lists must NOT catch these.
        """
        issues = _scan(src_dir, "src/synthorg/x.py", fixture)
        assert not issues, (label, issues)


class TestMarkerHelpers:
    """Direct unit tests for marker-detection helpers."""

    def test_has_marker_with_reason_dash(self) -> None:
        result = _MODULE._has_marker_with_reason(  # type: ignore[attr-defined]
            "# Phase 1  # lint-allow: migration-framing -- valid reason"
        )
        assert result is True

    def test_has_marker_with_reason_colon(self) -> None:
        result = _MODULE._has_marker_with_reason(  # type: ignore[attr-defined]
            "# Phase 1  # lint-allow: migration-framing: alt-sep reason"
        )
        assert result is True

    def test_has_marker_with_reason_dash_empty(self) -> None:
        result = _MODULE._has_marker_with_reason(  # type: ignore[attr-defined]
            "# Phase 1  # lint-allow: migration-framing --   "
        )
        assert result is False

    def test_has_marker_with_reason_colon_empty(self) -> None:
        result = _MODULE._has_marker_with_reason(  # type: ignore[attr-defined]
            "# Phase 1  # lint-allow: migration-framing:    "
        )
        assert result is False

    def test_has_marker_no_separator(self) -> None:
        result = _MODULE._has_marker_with_reason(  # type: ignore[attr-defined]
            "# Phase 1  # lint-allow: migration-framing"
        )
        assert result is False

    def test_has_marker_absent(self) -> None:
        result = _MODULE._has_marker_with_reason(  # type: ignore[attr-defined]
            "# just a normal comment"
        )
        assert result is False

    def test_dedicated_marker_python_comment(self) -> None:
        result = _MODULE._line_has_dedicated_marker(  # type: ignore[attr-defined]
            "# lint-allow: migration-framing -- documenting the rule"
        )
        assert result is True

    def test_dedicated_marker_sql_comment(self) -> None:
        result = _MODULE._line_has_dedicated_marker(  # type: ignore[attr-defined]
            "-- lint-allow: migration-framing -- in-revision pattern fixture"
        )
        assert result is True

    def test_dedicated_marker_code_line_rejected(self) -> None:
        """Marker after code (line does NOT start with ``#`` / ``--``) is rejected."""
        result = _MODULE._line_has_dedicated_marker(  # type: ignore[attr-defined]
            'x = "# lint-allow: migration-framing -- inline"'
        )
        assert result is False

    def test_trailing_marker_python_in_string_literal(self) -> None:
        result = _MODULE._line_has_trailing_marker_python(  # type: ignore[attr-defined]
            'x = "# lint-allow: migration-framing -- ok"\n'
        )
        assert result is False

    def test_trailing_marker_sql_basic(self) -> None:
        result = _MODULE._line_has_trailing_marker_sql(  # type: ignore[attr-defined]
            "SELECT 1; -- lint-allow: migration-framing -- example"
        )
        assert result is True


class TestErrorPaths:
    """Fail-closed semantics for tokenize / I/O errors."""

    def test_tokenize_error_does_not_suppress(self, src_dir: Path) -> None:
        """A syntactically broken line cannot tokenize; marker is unreachable."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "def f(:  # lint-allow: migration-framing -- broken\n# Phase 1\n",
        )
        assert any("phase" in i.lower() for i in issues), issues

    def test_scan_file_io_error_emits_sentinel(self, src_dir: Path) -> None:
        """Files that cannot be read produce an ``[I/O ERROR]``-prefixed entry."""
        # Directory at a ``.py`` path triggers OSError on read_text.
        fp = src_dir / "src" / "synthorg" / "fakedir.py"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.mkdir()
        rel = fp.relative_to(src_dir).as_posix()
        issues: list[str] = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert len(issues) == 1
        assert issues[0].startswith("[I/O ERROR] "), issues
        assert "fakedir.py" in issues[0]

    def test_violation_message_format(self, src_dir: Path) -> None:
        """Policy-violation messages use ``<rel>:<line>: <label>: <content>``."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Phase 1: Decompose\n",
        )
        assert len(issues) >= 1
        assert issues[0].startswith("src/synthorg/x.py:1: "), issues
