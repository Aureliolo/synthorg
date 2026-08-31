"""Unit tests for ``scripts/check_gh_slurp_not_with_jq.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

The load-bearing case is the CONTINUATION join: every real occurrence
writes ``--slurp`` and ``--jq`` on different physical lines joined by
backslash-newline, so a line-at-a-time scan sees neither flag beside the
other and reports nothing. The three defects that shipped were all of
that shape.

Covers:

* The exact three shapes that shipped in ``release-cut.yml``.
* The corrected form (slurp, then a separate ``jq``), which must pass.
* Short flags ``-q`` / ``-t`` and ``--template``.
* Continuation joining across two and three lines.
* Negative cases: ``--slurp`` alone, ``--jq`` alone, ``--slurpfile``
  (a jq flag that merely starts with the same letters), and a non-``gh``
  command that happens to carry both words.
* Reported line number is the statement's FIRST line, where the reader edits.
* Unreadable files fail closed rather than silently passing.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_gh_slurp_not_with_jq.py"


def _load_script_module() -> ModuleType:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_gh_slurp_not_with_jq",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _scan(tmp_path: Path, content: str) -> list[tuple[int, str]]:
    """Run ``_scan_file`` against a temp YAML fixture."""
    target = tmp_path / "workflow.yml"
    target.write_text(content, encoding="utf-8")
    hits: list[tuple[int, str]] = _MODULE._scan_file(target)
    return hits


class TestShippedShapes:
    """The three call sites that actually broke a release."""

    @pytest.mark.parametrize(
        "assignment",
        [
            pytest.param("STATE", id="throttle_state_read"),
            pytest.param("EXISTING", id="sticky_id_read"),
            pytest.param("COMMENT", id="mirror_body_read"),
        ],
    )
    def test_detects_the_form_that_shipped(
        self, tmp_path: Path, assignment: str
    ) -> None:
        content = (
            f'          {assignment}=$(gh api "repos/$R/issues/$N/comments" \\\n'
            "            --paginate --slurp \\\n"
            '            --jq "$SELECT | first | .id // empty")\n'
        )
        hits = _scan(tmp_path, content)
        assert len(hits) == 1

    def test_corrected_form_passes(self, tmp_path: Path) -> None:
        """Slurp, then a SEPARATE jq: the whole point of the fix."""
        content = (
            '          EXISTING=$(gh api "repos/$R/issues/$N/comments" \\\n'
            "            --paginate --slurp \\\n"
            '            | jq -r "$SELECT | first | .id // empty")\n'
        )
        assert _scan(tmp_path, content) == []


class TestFlagForms:
    """Every spelling gh accepts for the filter flags."""

    @pytest.mark.parametrize(
        "flag",
        [
            pytest.param("--jq", id="long_jq"),
            pytest.param("-q", id="short_jq"),
            pytest.param("--template", id="long_template"),
            pytest.param("-t", id="short_template"),
        ],
    )
    def test_detects_each_filter_flag(self, tmp_path: Path, flag: str) -> None:
        content = f'gh api "repos/$R/x" --paginate --slurp {flag} ".[]"\n'
        assert len(_scan(tmp_path, content)) == 1

    def test_slurpfile_is_not_slurp(self, tmp_path: Path) -> None:
        """``--slurpfile`` is a jq flag; it is not ``gh``'s ``--slurp``."""
        content = 'jq -n --slurpfile additions "$f" --arg q "$Q" \'{}\'\n'
        assert _scan(tmp_path, content) == []


class TestContinuationJoining:
    """The reason a naive line scan reports nothing on every real case."""

    def test_flags_split_across_two_lines(self, tmp_path: Path) -> None:
        content = 'gh api "$P" --paginate --slurp \\\n  --jq ".[]"\n'
        assert len(_scan(tmp_path, content)) == 1

    def test_flags_split_across_three_lines(self, tmp_path: Path) -> None:
        content = 'gh api "$P" \\\n  --paginate --slurp \\\n  --jq ".[]"\n'
        assert len(_scan(tmp_path, content)) == 1

    def test_reports_the_statements_first_line(self, tmp_path: Path) -> None:
        """The reader edits the opening line, not wherever the flag landed."""
        content = (
            '# leading comment\ngh api "$P" \\\n  --paginate --slurp \\\n  --jq ".[]"\n'
        )
        hits = _scan(tmp_path, content)
        assert len(hits) == 1
        assert hits[0][0] == 2

    def test_separate_statements_do_not_merge(self, tmp_path: Path) -> None:
        """Without a trailing backslash the two calls stay independent."""
        content = 'gh api "$P" --paginate --slurp\ngh api "$Q" --jq ".[]"\n'
        assert _scan(tmp_path, content) == []


class TestNegatives:
    """Shapes that are legitimate and must never be flagged."""

    @pytest.mark.parametrize(
        ("label", "content"),
        [
            ("slurp_alone", 'gh api "$P" --paginate --slurp\n'),
            ("jq_alone", 'gh api "$P" --paginate --jq ".[]"\n'),
            ("not_a_gh_call", 'echo "--slurp and --jq are exclusive"\n'),
            ("curl_with_both_words", 'curl --slurp --jq "$X"\n'),
        ],
    )
    def test_not_flagged(self, tmp_path: Path, label: str, content: str) -> None:
        assert _scan(tmp_path, content) == [], label

    def test_gh_retry_wrapper_is_still_a_gh_call(self, tmp_path: Path) -> None:
        """``gh_retry gh api`` is the repo's retry wrapper, not an exemption."""
        content = 'gh_retry gh api "$P" --paginate --slurp --jq ".[]"\n'
        assert len(_scan(tmp_path, content)) == 1


class TestFailClosed:
    """A file the gate cannot read is a violation, never a pass."""

    def test_unreadable_file_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.yml"
        target.write_bytes(b"\xff\xfe gh api --paginate --slurp --jq x")
        with pytest.raises(_MODULE._UnreadableFileError):
            _MODULE._scan_file(target)
