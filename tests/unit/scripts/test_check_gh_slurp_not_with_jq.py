"""Unit tests for ``scripts/check_gh_slurp_not_with_jq.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

Two properties carry most of the weight, because a gate is only worth what
it refuses AND what it lets through:

* Statement reconstruction. A workflow writes one ``gh api`` call across
  several physical lines, joined by a backslash or by a YAML folded
  scalar, so a line-at-a-time scan sees neither flag beside the other and
  reports nothing at all.
* Command scoping. The flags count only within one shell command. Read
  across a pipe, the ``-q`` of a downstream ``grep`` looks exactly like
  gh's own filter flag, and the gate rejects the form it recommends.

Covers both rules, the fail-closed entry points, and a dogfood run against
the real tree.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_gh_slurp_not_with_jq.py"

_SLURP_RULE = "slurp-with-filter"
_AGGREGATE_RULE = "paginate-aggregate"


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


def _write(tmp_path: Path, content: str, name: str = "workflow.yml") -> Path:
    """Write a fixture verbatim, without newline translation."""
    target = tmp_path / name
    target.write_bytes(content.encode("utf-8"))
    return target


def _rules(tmp_path: Path, content: str) -> list[str]:
    """Rule ids reported for a fixture, in order."""
    return [hit.rule for hit in _MODULE._scan_file(_write(tmp_path, content))]


def _hits(tmp_path: Path, content: str) -> list[tuple[int, str, str]]:
    """Full ``(lineno, rule, command)`` triples for a fixture."""
    return [
        (hit.lineno, hit.rule, hit.command)
        for hit in _MODULE._scan_file(_write(tmp_path, content))
    ]


class TestRealCallSiteShapes:
    """The continuation-joined assignment form workflow call sites use."""

    def test_detects_the_continuation_joined_form(self, tmp_path: Path) -> None:
        content = (
            'STATE=$(gh api "repos/$R/issues/$N/comments" \\\n'
            "  --paginate --slurp \\\n"
            '  --jq "$SELECT | first | .body // empty")\n'
        )
        expected = (
            'STATE=$(gh api "repos/$R/issues/$N/comments" --paginate '
            '--slurp --jq "$SELECT | first | .body // empty")'
        )
        assert _hits(tmp_path, content) == [(1, _SLURP_RULE, expected)]

    def test_corrected_form_passes(self, tmp_path: Path) -> None:
        """Slurp piped into a separate jq is the compliant form."""
        content = (
            'EXISTING=$(gh api "repos/$R/issues/$N/comments" \\\n'
            "  --paginate --slurp \\\n"
            '  | jq -r "$SELECT | first | .id // empty")\n'
        )
        assert _rules(tmp_path, content) == []

    def test_pipes_inside_the_jq_program_are_not_shell_pipes(
        self, tmp_path: Path
    ) -> None:
        """A quoted jq program is one argument, however many bars it holds."""
        content = 'gh api "$P" --paginate --slurp --jq "a | first | .id // empty"\n'
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    def test_multiple_violations_in_one_file(self, tmp_path: Path) -> None:
        """The shape that shipped: several call sites in a single workflow."""
        content = (
            'STATE=$(gh api "$P" --paginate --slurp --jq ".a")\n'
            "echo unrelated\n"
            'EXISTING=$(gh api "$Q" --paginate --slurp -q ".b")\n'
            "echo unrelated\n"
            'COMMENT=$(gh api "$R" --paginate --slurp --template "{{.}}")\n'
        )
        assert [(line, rule) for line, rule, _ in _hits(tmp_path, content)] == [
            (1, _SLURP_RULE),
            (3, _SLURP_RULE),
            (5, _SLURP_RULE),
        ]


class TestFlagForms:
    """Every spelling gh accepts for the filter flags."""

    @pytest.mark.parametrize(
        "flag",
        [
            pytest.param('--jq ".[]"', id="long_jq"),
            pytest.param('-q ".[]"', id="short_jq"),
            pytest.param('--template "{{.}}"', id="long_template"),
            pytest.param('-t "{{.}}"', id="short_template"),
            pytest.param('--jq=".[]"', id="jq_equals_form"),
        ],
    )
    def test_detects_each_filter_flag(self, tmp_path: Path, flag: str) -> None:
        content = f'gh api "repos/$R/x" --paginate --slurp {flag}\n'
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    def test_slurpfile_is_not_slurp(self, tmp_path: Path) -> None:
        """``--slurpfile`` is a jq flag, not gh's ``--slurp``.

        The fixture carries a real ``gh api`` call so the ``--slurp``
        boundary is the predicate that decides; without one the check
        short-circuits earlier and would pass however wrong that
        boundary was.
        """
        content = "gh api \"$P\" --paginate --jq '.[].x' | jq -n --slurpfile a f '{}'\n"
        assert _rules(tmp_path, content) == []

    def test_filter_lookalike_is_not_a_filter(self, tmp_path: Path) -> None:
        """Symmetric to ``--slurpfile``: a longer flag is a different flag."""
        content = 'gh api "$P" --paginate --slurp --jqx ".[]"\n'
        assert _rules(tmp_path, content) == []

    def test_flag_order_is_irrelevant(self, tmp_path: Path) -> None:
        """gh does not care which flag came first, so neither may the gate."""
        content = 'gh api "$P" --paginate --jq ".[]" --slurp\n'
        assert _rules(tmp_path, content) == [_SLURP_RULE]


class TestGhInvocationForms:
    """Which spellings of the command itself count as a ``gh api`` call."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param('gh api "$P"', id="plain"),
            pytest.param('gh_retry gh api "$P"', id="retry_wrapper"),
            pytest.param('gh -R owner/repo api "$P"', id="short_global_flag"),
            pytest.param('gh --repo "$X" api "$P"', id="long_global_flag_with_value"),
        ],
    )
    def test_detected(self, tmp_path: Path, command: str) -> None:
        content = f'{command} --paginate --slurp --jq ".[]"\n'
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    def test_gh_api_retry_wrapper_forwards_its_arguments(self, tmp_path: Path) -> None:
        """The repo's other wrapper passes flags straight through to gh api."""
        content = "gh_api_retry --paginate \"$P\" --jq '.[0].id'\n"
        assert _rules(tmp_path, content) == [_AGGREGATE_RULE]

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param('gh pr view 1 --slurp --jq ".[]"\n', id="other_subcommand"),
            pytest.param('curl --slurp --jq "$X"\n', id="not_gh"),
            pytest.param(
                'echo "--slurp and --jq are exclusive"\n', id="prose_in_a_string"
            ),
        ],
    )
    def test_not_a_gh_api_call(self, tmp_path: Path, content: str) -> None:
        assert _rules(tmp_path, content) == []


class TestCommandScoping:
    """Flags belong to one command, not to everything joined around it."""

    @pytest.mark.parametrize(
        "downstream",
        [
            pytest.param("grep -q x", id="grep_q"),
            pytest.param("column -t", id="column_t"),
            pytest.param("sort -t, -k1", id="sort_t"),
            pytest.param("read -t 5 line", id="read_t"),
        ],
    )
    def test_downstream_short_flag_is_not_ghs_filter(
        self, tmp_path: Path, downstream: str
    ) -> None:
        """The recommended form plus an ordinary pipe stage must still pass."""
        content = (
            'gh api "$P" --paginate --slurp \\\n'
            '  | jq -r ".[].id" \\\n'
            f"  | {downstream}\n"
        )
        assert _rules(tmp_path, content) == []

    def test_only_the_offending_command_is_reported(self, tmp_path: Path) -> None:
        """Two commands split by a pipe; the clean one is left alone."""
        content = (
            'gh api url1 --jq ".x" \\\n  | gh api url2 --paginate --slurp --jq ".y"\n'
        )
        assert [(line, rule) for line, rule, _ in _hits(tmp_path, content)] == [
            (2, _SLURP_RULE)
        ]

    def test_backslash_joined_calls_are_one_command(self, tmp_path: Path) -> None:
        """A trailing backslash makes the second call the first one's argv.

        gh therefore receives every flag on the joined line, so this is a
        single violating invocation reported once, at the line the command
        starts on.
        """
        content = 'gh api url1 --paginate --slurp \\\ngh api url2 --jq ".thing"\n'
        assert [(line, rule) for line, rule, _ in _hits(tmp_path, content)] == [
            (1, _SLURP_RULE)
        ]


class TestStatementReconstruction:
    """Why a line-at-a-time scan reports nothing on every real occurrence."""

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(
                'gh api "$P" --paginate --slurp \\\n  --jq ".[]"\n', id="two_lines"
            ),
            pytest.param(
                'gh api "$P" \\\n  --paginate --slurp \\\n  --jq ".[]"\n',
                id="three_lines",
            ),
        ],
    )
    def test_flags_split_across_continuation_lines(
        self, tmp_path: Path, content: str
    ) -> None:
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    def test_reports_the_commands_first_line(self, tmp_path: Path) -> None:
        """The reader edits the opening line, not wherever the flag landed."""
        content = (
            '# leading comment\ngh api "$P" \\\n  --paginate --slurp \\\n  --jq ".[]"\n'
        )
        assert [line for line, _, _ in _hits(tmp_path, content)] == [2]

    def test_separate_statements_do_not_merge(self, tmp_path: Path) -> None:
        """Without a trailing backslash the two calls stay independent."""
        content = 'gh api "$P" --paginate --slurp\ngh api "$Q" --jq ".[]"\n'
        assert _rules(tmp_path, content) == []

    def test_escaped_backslash_is_not_a_continuation(self, tmp_path: Path) -> None:
        """An even run of trailing backslashes ends the statement."""
        content = 'echo "trailing \\\\"\ngh api "$P" --paginate --jq ".[].id"\n'
        assert _rules(tmp_path, content) == []

    def test_folded_block_scalar_is_one_statement(self, tmp_path: Path) -> None:
        """``run: >`` is joined by YAML, so no backslash ever appears."""
        content = (
            "      - name: read the sticky comment\n"
            "        run: >-\n"
            '          gh api "$P"\n'
            "          --paginate --slurp\n"
            '          --jq ".[] | first"\n'
        )
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    def test_literal_block_scalar_keeps_its_lines(self, tmp_path: Path) -> None:
        """``run: |`` preserves newlines, so unjoined lines stay separate."""
        content = (
            "        run: |\n"
            '          gh api "$P" --paginate --slurp\n'
            '          gh api "$Q" --jq ".[]"\n'
        )
        assert _rules(tmp_path, content) == []

    def test_crlf_continuation(self, tmp_path: Path) -> None:
        """Workflow files edited on Windows still reconstruct correctly."""
        content = 'gh api "$P" --paginate --slurp \\\r\n  --jq ".[]"\r\n'
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param("", [], id="empty_file"),
            pytest.param(
                'gh api "$P" --paginate --slurp --jq ".[]"',
                [_SLURP_RULE],
                id="no_trailing_newline",
            ),
            pytest.param(
                'gh api "$P" --paginate --slurp --jq ".[]" \\\n',
                [_SLURP_RULE],
                id="trailing_backslash_on_last_line",
            ),
        ],
    )
    def test_file_boundaries(
        self, tmp_path: Path, content: str, expected: list[str]
    ) -> None:
        assert _rules(tmp_path, content) == expected

    def test_dangling_backslash_is_not_reported(self, tmp_path: Path) -> None:
        """A continuation with nothing to join to leaves no stray character."""
        content = 'gh api "$P" --paginate --slurp --jq ".[]" \\\n'
        statement = _hits(tmp_path, content)[0][2]
        assert not statement.endswith("\\")


class TestComments:
    """Prose about these flags is documentation, not an invocation."""

    def test_comment_discussing_the_flags_is_not_code(self, tmp_path: Path) -> None:
        content = (
            "# --slurp because --paginate --jq applies the filter per page\n"
            'gh api "$P" --paginate --slurp | jq -r ".[]"\n'
        )
        assert _rules(tmp_path, content) == []

    def test_comment_ending_in_a_backslash_does_not_join_code(
        self, tmp_path: Path
    ) -> None:
        """A shell comment runs to the physical end of line, backslash or not."""
        content = (
            "#   gh attestation verify oci://x -R owner/repo \\\n"
            'gh api "$P" --paginate --slurp | jq -r ".[]"\n'
        )
        assert _rules(tmp_path, content) == []

    def test_hash_inside_a_quoted_program_is_not_a_comment(
        self, tmp_path: Path
    ) -> None:
        content = 'gh api "$P" --paginate --slurp --jq \'test("#x")\'\n'
        assert _rules(tmp_path, content) == [_SLURP_RULE]


class TestPaginateAggregateRule:
    """``--paginate`` with a collapsing filter and no ``--slurp`` to fold."""

    @pytest.mark.parametrize(
        "selector",
        [
            pytest.param(".[0].id // empty", id="index_zero"),
            pytest.param("first | .id", id="first"),
            pytest.param("last | .id", id="last"),
            pytest.param("length", id="length"),
            pytest.param("map(.n) | add", id="add"),
        ],
    )
    def test_collapsing_selector_per_page(self, tmp_path: Path, selector: str) -> None:
        content = f"gh api \"$P\" --paginate --jq '{selector}'\n"
        assert _rules(tmp_path, content) == [_AGGREGATE_RULE]

    @pytest.mark.parametrize(
        "selector",
        [
            pytest.param(".[].author.login | select(.)", id="stream_field"),
            pytest.param('.[] | select(.body | startswith("x")) | .id', id="stream"),
            pytest.param(".[].ref", id="stream_ref"),
        ],
    )
    def test_streaming_filter_is_correct_per_page(
        self, tmp_path: Path, selector: str
    ) -> None:
        """Per-page streaming concatenates to the same answer, so it passes."""
        content = f"gh api \"$P\" --paginate --jq '{selector}'\n"
        assert _rules(tmp_path, content) == []

    def test_slurp_present_is_rule_one_not_rule_two(self, tmp_path: Path) -> None:
        content = "gh api \"$P\" --paginate --slurp --jq 'first | .id'\n"
        assert _rules(tmp_path, content) == [_SLURP_RULE]

    def test_no_paginate_is_not_a_violation(self, tmp_path: Path) -> None:
        """Without ``--paginate`` there is one page and one answer."""
        content = "gh api \"$P\" --jq '.[0].id'\n"
        assert _rules(tmp_path, content) == []

    def test_opt_out_marker_is_honoured(self, tmp_path: Path) -> None:
        content = (
            "gh api \"$P\" --paginate --jq '.[0].id'  "
            "# lint-allow: paginate-aggregate -- recombined by jq -s downstream\n"
        )
        assert _rules(tmp_path, content) == []

    def test_opt_out_marker_needs_a_reason(self, tmp_path: Path) -> None:
        content = (
            "gh api \"$P\" --paginate --jq '.[0].id'  "
            "# lint-allow: paginate-aggregate\n"
        )
        assert _rules(tmp_path, content) == [_AGGREGATE_RULE]

    def test_opt_out_does_not_reach_rule_one(self, tmp_path: Path) -> None:
        """An unrunnable invocation is never worth preserving."""
        content = (
            "gh api \"$P\" --paginate --slurp --jq '.[]'  "
            "# lint-allow: paginate-aggregate -- not applicable\n"
        )
        assert _rules(tmp_path, content) == [_SLURP_RULE]


class TestScanAll:
    """``--scan-all`` walks the tree and refuses to pass on an empty one."""

    def test_reports_violations_under_github(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        (root / "workflows").mkdir(parents=True)
        _write(
            root / "workflows",
            'gh api "$P" --paginate --slurp --jq ".[]"\n',
            name="w.yml",
        )
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.cmd_scan_all() == 1

    def test_scans_composite_actions_not_only_workflows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The walk covers all of ``.github/``, composites included."""
        action = tmp_path / ".github" / "actions" / "thing"
        action.mkdir(parents=True)
        _write(action, 'gh api "$P" --paginate --slurp --jq ".[]"\n', name="action.yml")
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", tmp_path / ".github")
        assert _MODULE.cmd_scan_all() == 1

    def test_clean_tree_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        root.mkdir()
        _write(root, 'gh api "$P" --paginate --slurp | jq -r ".[]"\n', name="w.yml")
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.cmd_scan_all() == 0

    def test_missing_github_root_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scanning nothing must not read as a clean repository."""
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", tmp_path / "absent")
        assert _MODULE.cmd_scan_all() == 2

    def test_empty_github_root_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        root.mkdir()
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.cmd_scan_all() == 2

    def test_real_tree_is_clean(self) -> None:
        """Dogfood: the shipped workflows, comments and all, pass the gate."""
        assert _MODULE.cmd_scan_all() == 0


class TestScanPaths:
    """Per-path mode, and its own refusal to pass on an empty selection."""

    def test_reports_a_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        root.mkdir()
        target = _write(
            root, 'gh api "$P" --paginate --slurp --jq ".[]"\n', name="w.yml"
        )
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.cmd_scan_paths([str(target)]) == 1

    def test_clean_path_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        root.mkdir()
        target = _write(root, 'gh api "$P" --paginate --slurp | jq -r ".[]"\n')
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.cmd_scan_paths([str(target)]) == 0

    def test_no_paths_supplied_is_not_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pre-commit hands an empty list when nothing matched its filter."""
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", tmp_path)
        assert _MODULE.cmd_scan_paths([]) == 0

    @pytest.mark.parametrize(
        ("name", "under_github"),
        [
            pytest.param("notes.txt", True, id="wrong_suffix"),
            pytest.param("w.yml", False, id="outside_github"),
        ],
    )
    def test_every_path_filtered_out_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        under_github: bool,
    ) -> None:
        """Receiving files and scanning none of them is a caller mismatch."""
        root = tmp_path / ".github"
        root.mkdir()
        home = root if under_github else tmp_path
        target = _write(home, 'gh api "$P" --slurp --jq ".[]"\n', name=name)
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.cmd_scan_paths([str(target)]) == 2


class TestReporting:
    """What a developer actually sees when the gate fires."""

    def test_violation_line_names_path_line_and_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        root.mkdir()
        target = _write(
            root, 'gh api "$P" --paginate --slurp --jq ".[]"\n', name="w.yml"
        )
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        violations = _MODULE._collect([target])
        assert len(violations) == 1
        assert f":1: [{_SLURP_RULE}]" in violations[0]

    def test_report_prints_the_steering_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _MODULE._report(["some/file.yml:1: [x] y"]) == 1
        captured = capsys.readouterr()
        assert "some/file.yml:1: [x] y" in captured.out
        assert "--slurp" in captured.err
        assert "flatten(1)" in captured.err

    def test_report_is_silent_when_clean(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _MODULE._report([]) == 0
        assert capsys.readouterr().out == ""

    def test_steering_message_names_every_detected_flag(self) -> None:
        """A developer cannot act on a flag the message does not mention."""
        for flag in ("--jq", "-q", "--template", "-t"):
            assert flag in _MODULE._STEERING_MESSAGE

    def test_rel_falls_back_for_a_path_outside_the_repo(self, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere.yml"
        assert _MODULE._rel(outside) == str(outside)


class TestMain:
    """Argument dispatch."""

    def test_scan_all_flag_dispatches_to_the_tree_walk(self) -> None:
        assert _MODULE.main(["--scan-all"]) == 0

    def test_paths_dispatch_to_per_path_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / ".github"
        root.mkdir()
        target = _write(root, 'gh api "$P" --paginate --slurp --jq ".[]"\n')
        monkeypatch.setattr(_MODULE, "_GITHUB_ROOT", root)
        assert _MODULE.main([str(target)]) == 1

    def test_no_arguments_is_clean(self) -> None:
        assert _MODULE.main([]) == 0


class TestFailClosed:
    """A file the gate cannot read is a violation, never a pass."""

    def test_unreadable_file_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.yml"
        target.write_bytes(b"\xff\xfe gh api --paginate --slurp --jq x")
        with pytest.raises(_MODULE._UnreadableFileError, match=r"bad\.yml"):
            _MODULE._scan_file(target)

    def test_unreadable_file_becomes_a_violation(self, tmp_path: Path) -> None:
        """``_collect`` promotes the raise so the run cannot report clean."""
        target = tmp_path / "bad.yml"
        target.write_bytes(b"\xff\xfe gh api --paginate --slurp --jq x")
        violations = _MODULE._collect([target])
        assert len(violations) == 1
        assert "could not read file" in violations[0]
