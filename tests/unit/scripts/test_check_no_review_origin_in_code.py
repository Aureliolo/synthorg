"""Unit tests for ``scripts/check_no_review_origin_in_code.py``.

Loads the script as a module so its private helpers are callable
without spawning subprocesses (the script's project-root discovery
is anchored at ``__file__``).

Covers:

* Positive matches for every forbidden pattern.
* Negative cases for look-alikes (``re-exported from``, ``(#a)``,
  bug-tracker URLs, ``# noqa: SEC-1`` style markers).
* Per-line opt-out (``# lint-allow: review-origin -- <reason>``).
* Path allowlist (docs / scanner self-test files).
* Path-traversal guard.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_review_origin_in_code.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_no_review_origin_in_code",
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


class TestReviewerCitations:
    """Reviewer-origin citations are flagged."""

    def test_pre_pr_review_lowercase(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir, "src/synthorg/x.py", "# pre-PR review #1234: do thing\n"
        )
        assert any("pre-PR review" in i for i in issues), issues

    def test_pre_pr_review_capitalized(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir, "src/synthorg/x.py", "# Pre-PR review #1234 surfaced this\n"
        )
        assert any("pre-pr review" in i.lower() for i in issues), issues

    def test_coderabbit_brand(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# CodeRabbit at foo.py:12 flagged this\n",
        )
        assert any("CodeRabbit" in i for i in issues), issues

    def test_round_n_fix(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "tests/unit/x.py",
            '"""Round-3 fix: do not persist discovered models."""\n',
        )
        assert any("Round-" in i for i in issues), issues

    def test_round_n_review(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir, "src/synthorg/x.py", "# round-12 review surfaced this\n"
        )
        assert any("round-" in i.lower() for i in issues), issues


class TestIssueBackRefs:
    """In-code issue / PR back-references are flagged."""

    def test_paren_hash_nnnn(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# context: (#1234 added this column)\n",
        )
        assert any("(#" in i for i in issues), issues

    def test_paren_hash_nnnn_short_digits_skipped(self, src_dir: Path) -> None:
        """``(#a)`` and ``(#12)`` placeholders are NOT flagged (3+ digits)."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# placeholder (#a) and (#12) and (#xx)\n",
        )
        assert issues == [], issues

    def test_as_part_of(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# implemented as part of #1234\n",
        )
        assert any("part of #" in i.lower() for i in issues), issues

    def test_fixes_n(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "src/synthorg/x.py", "# fixes #1234 in the auth flow\n")
        assert any("fixes #" in i.lower() for i in issues), issues

    def test_see_pr(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "src/synthorg/x.py", "# see PR #1234 for context\n")
        assert any("see pr #" in i.lower() for i in issues), issues

    def test_gh_dash_n(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "src/synthorg/x.py", "# workaround for GH-1234\n")
        assert any("GH-" in i for i in issues), issues

    def test_issue_n_narrative(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Issue #1666 B-5: downgrade to DEBUG\n",
        )
        assert any("issue #" in i.lower() for i in issues), issues

    def test_closes_n(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "src/synthorg/x.py", "# closes #1234\n")
        assert any("closes #" in i.lower() for i in issues), issues


class TestNakedSecTaxonomy:
    """Naked ``SEC-N`` tokens in ``src/synthorg/`` and ``tests/`` are flagged."""

    def test_sec_1_in_src(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/api/x.py",
            "# SEC-1: drop exc_info=True\n",
        )
        assert any("SEC-" in i for i in issues), issues

    def test_sec_2_in_tests(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "tests/unit/x.py",
            '"""these are SEC-2 audit requirements"""\n',
        )
        assert any("SEC-" in i for i in issues), issues

    def test_sec_n_in_string_literal(self, src_dir: Path) -> None:
        """SEC-N inside a string literal (e.g. log message) is also flagged."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'msg = "SEC-1 redacted error"\n',
        )
        assert any("SEC-" in i for i in issues), issues


class TestNegatives:
    """Look-alike patterns must NOT fire."""

    def test_re_exported_from(self, src_dir: Path) -> None:
        """``re-exported from`` is not migration framing."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            '"""Public surface (re-exported from ws.py)."""\n',
        )
        assert issues == [], issues

    def test_imported_from(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Builders are imported from helpers.py\n",
        )
        assert issues == [], issues

    def test_bug_tracker_url(self, src_dir: Path) -> None:
        """External bug-tracker URLs are allowed."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Workaround for https://github.com/python/cpython/issues/123\n",
        )
        assert issues == [], issues

    def test_paren_hash_short(self, src_dir: Path) -> None:
        """Two-digit numbers and placeholders do not match."""
        issues = _scan(src_dir, "src/synthorg/x.py", "# stage (#12) is a placeholder\n")
        assert issues == [], issues

    def test_lint_allow_marker_self_reference(self, src_dir: Path) -> None:
        """Lines that DEFINE the marker syntax are not themselves matches."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# The marker is # lint-allow: review-origin -- reason\n",
        )
        # The line contains "lint-allow: review-origin" prose but does not
        # contain any forbidden token, so it should not fire.
        assert issues == [], issues

    def test_url_with_section_anchor(self, src_dir: Path) -> None:
        """``#section1`` URL anchors are not back-refs."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# See https://example.com/docs#section1234 for details\n",
        )
        assert issues == [], issues

    def test_short_hash_in_text(self, src_dir: Path) -> None:
        """Bare ``#12`` (2 digits) is not a back-ref."""
        issues = _scan(src_dir, "src/synthorg/x.py", "# count is #12 of 50\n")
        assert issues == [], issues

    def test_round_verb_not_flagged(self, src_dir: Path) -> None:
        """The verb ``round`` (lowercase) before a number is not framing."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# would round 0.5 down using int() truncation\n",
        )
        assert issues == [], issues

    def test_round_proper_noun_flagged(self, src_dir: Path) -> None:
        """Capital ``Round N`` IS framing (no dash, no verb confusion)."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# Round 6's shallow merge surfaced this\n",
        )
        assert any("Round" in i for i in issues), issues


class TestSuppressionMarker:
    """``# lint-allow: review-origin -- <reason>`` suppresses with reason."""

    def test_marker_with_reason_same_line(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# CodeRabbit  # lint-allow: review-origin -- discussing the rule\n",
        )
        assert issues == [], issues

    def test_marker_with_reason_preceding_line(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            (
                "# lint-allow: review-origin -- documenting the rule shape\n"
                "# pre-PR review #1234 surfaced this\n"
            ),
        )
        assert issues == [], issues

    def test_marker_without_reason_does_not_suppress(self, src_dir: Path) -> None:
        """Empty justification must NOT suppress."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# CodeRabbit  # lint-allow: review-origin\n",
        )
        assert any("CodeRabbit" in i for i in issues), issues

    def test_marker_whitespace_reason_does_not_suppress(self, src_dir: Path) -> None:
        """Whitespace-only justification must NOT suppress."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            "# CodeRabbit  # lint-allow: review-origin --   \n",
        )
        assert any("CodeRabbit" in i for i in issues), issues

    def test_marker_inside_string_literal_does_not_suppress(
        self, src_dir: Path
    ) -> None:
        """Marker inside string literals must NOT suppress code on the same line."""
        issues = _scan(
            src_dir,
            "src/synthorg/x.py",
            'x = "# lint-allow: review-origin -- ok"; y = "CodeRabbit"\n',
        )
        assert any("CodeRabbit" in i for i in issues), issues


class TestPathAllowlist:
    """Files under canonical-doc paths are not scanned."""

    def test_docs_design_skipped(self, src_dir: Path) -> None:
        """``docs/design/<file>.md`` is the canonical SEC-N home."""
        fp = _write_fixture(
            src_dir,
            "docs/design/security.md",
            "We use the SEC-1 taxonomy and pre-PR review #1234.\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_docs_reference_skipped(self, src_dir: Path) -> None:
        fp = _write_fixture(
            src_dir,
            "docs/reference/conventions.md",
            "Forbidden: pre-PR review #N, CodeRabbit, Round-N.\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_changelog_skipped(self, src_dir: Path) -> None:
        fp = _write_fixture(
            src_dir,
            "CHANGELOG.md",
            "* fixes (#1234) (#5678) (#9012)\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_scanner_self_test_skipped(self, src_dir: Path) -> None:
        """The gate's own test fixtures need forbidden patterns inline."""
        fp = _write_fixture(
            src_dir,
            "tests/unit/scripts/test_check_no_review_origin_in_code.py",
            'fixture = "# CodeRabbit at foo.py:1 -- pre-PR review #1234"\n',
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_scanner_self_skipped(self, src_dir: Path) -> None:
        """The gate script itself is allowed to mention forbidden tokens."""
        fp = _write_fixture(
            src_dir,
            "scripts/check_no_review_origin_in_code.py",
            'PATTERN = "pre-PR review #1234"\n',
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues


class TestScopeLimit:
    """Only ``*.py`` files under src/synthorg/ + tests/ are scanned."""

    def test_outside_scope_ignored(self, src_dir: Path) -> None:
        """A file under ``random/`` is outside scope."""
        fp = _write_fixture(src_dir, "random/x.py", "# pre-PR review #1234\n")
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues

    def test_non_python_text_skipped(self, src_dir: Path) -> None:
        """``.txt`` under src/synthorg/ is not scanned."""
        fp = _write_fixture(
            src_dir,
            "src/synthorg/notes.txt",
            "# pre-PR review #1234 for context\n",
        )
        rel = fp.relative_to(src_dir).as_posix()
        issues = _MODULE._scan_file(fp, rel)  # type: ignore[attr-defined]
        assert issues == [], issues


class TestPathTraversal:
    """``--paths ../..`` is rejected via the project-root anchor."""

    def test_resolve_root_inside(self, tmp_path: Path) -> None:
        """A path inside the project root resolves cleanly."""
        inside = tmp_path / "src" / "synthorg"
        inside.mkdir(parents=True)
        resolved = _MODULE._resolve_root(inside, tmp_path)  # type: ignore[attr-defined]
        assert resolved == inside.resolve()

    def test_resolve_root_outside(self, tmp_path: Path) -> None:
        """A path that escapes the project root returns None."""
        # Use ``..`` to climb out of the project root anchor.
        outside = Path("..") / ".." / "etc"
        resolved = _MODULE._resolve_root(outside, tmp_path)  # type: ignore[attr-defined]
        assert resolved is None
