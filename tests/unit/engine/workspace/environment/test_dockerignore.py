"""Unit tests for ``.dockerignore`` matching.

The packer is the only thing standing between an author's exclusions and
what leaves the host, so these pin the pattern syntax an author's file
was written against rather than the subset that happens to be easy.
"""

from pathlib import Path

import pytest

from synthorg.engine.workspace.environment._dockerignore import (
    load_dockerignore,
    parse_dockerignore,
)

pytestmark = pytest.mark.unit


class TestPatternSyntax:
    @pytest.mark.parametrize(
        ("pattern", "path", "excluded"),
        [
            ("*.env", "secrets.env", True),
            ("*.env", "config/secrets.env", False),
            ("*.env", "secrets.envx", False),
            ("**/*.env", "config/deep/secrets.env", True),
            ("**/*.env", "secrets.env", True),
            ("node_modules", "node_modules", True),
            ("node_modules", "node_modules/pkg/index.js", True),
            ("node_modules", "web/node_modules/pkg", False),
            ("*/tmp", "a/tmp/x", True),
            ("*/tmp", "a/b/tmp", False),
            ("?.txt", "a.txt", True),
            ("?.txt", "ab.txt", False),
            ("build/", "build/out", True),
            ("/build", "build/out", True),
        ],
    )
    def test_one_pattern(self, pattern: str, path: str, excluded: bool) -> None:
        assert parse_dockerignore(pattern).excludes(path) is excluded

    def test_a_regex_metacharacter_is_matched_literally(self) -> None:
        """An author writing ``v1.0+beta`` means those characters, not a regex."""
        matcher = parse_dockerignore("v1.0+beta\n")

        assert matcher.excludes("v1.0+beta") is True
        assert matcher.excludes("v1x0+beta") is False

    def test_a_caret_is_matched_literally(self) -> None:
        """moby's own escape set omits it, which compiles to a different pattern."""
        matcher = parse_dockerignore("^weird\n")

        assert matcher.excludes("^weird") is True
        assert matcher.excludes("weird") is False


class TestFileSemantics:
    def test_comments_and_blank_lines_contribute_nothing(self) -> None:
        matcher = parse_dockerignore("# a comment\n\n   \n*.log\n")

        assert matcher.excludes("app.log") is True
        assert matcher.excludes("a comment") is False

    def test_the_last_matching_rule_decides(self) -> None:
        """A ``!`` re-includes, and order is the whole contract."""
        matcher = parse_dockerignore("*.md\n!README.md\n")

        assert matcher.excludes("CHANGELOG.md") is True
        assert matcher.excludes("README.md") is False

    def test_a_later_exclusion_beats_an_earlier_re_inclusion(self) -> None:
        matcher = parse_dockerignore("!README.md\n*.md\n")

        assert matcher.excludes("README.md") is True

    def test_a_parent_match_excludes_the_tree_beneath_it(self) -> None:
        """Otherwise a directory rule would emit one empty directory entry."""
        matcher = parse_dockerignore("dist\n")

        assert matcher.excludes("dist/js/app.js") is True

    def test_an_empty_file_excludes_nothing(self) -> None:
        matcher = parse_dockerignore("")

        assert bool(matcher) is False
        assert matcher.excludes("anything") is False


class TestARefusedLineDoesNotFailTheBuild:
    """The file is agent-authored; the caller is packing a build context.

    Raising out of the parser unwinds the whole provision and reports it
    as a Dockerfile build failure that did not happen, so a line the
    packer will not take is dropped and the rest of the file still
    applies.
    """

    def test_an_unbalanced_bracket_is_dropped(self) -> None:
        """``[`` and ``]`` pass through unescaped, so this reaches re.compile."""
        matcher = parse_dockerignore("*.log\n[unclosed\n*.tmp\n")

        assert matcher.excludes("app.log") is True
        assert matcher.excludes("scratch.tmp") is True
        assert matcher.excludes("[unclosed") is False

    def test_a_pattern_past_the_length_ceiling_is_dropped(self) -> None:
        matcher = parse_dockerignore(f"*.log\n{'a' * 513}\n")

        assert matcher.excludes("app.log") is True
        assert matcher.excludes("a" * 513) is False

    def test_a_pattern_repeating_double_stars_is_dropped(self) -> None:
        """``**`` becomes ``(.*/)?``; nesting them backtracks catastrophically."""
        matcher = parse_dockerignore("*.log\n" + "**/" * 5 + "x\n")

        assert matcher.excludes("app.log") is True
        assert matcher.excludes("a/b/c/d/e/x") is False

    def test_the_ceilings_admit_a_pattern_written_on_purpose(self) -> None:
        """The bound has to sit above what an author actually writes."""
        matcher = parse_dockerignore("**/build/**/*.map\n")

        assert matcher.excludes("web/build/assets/app.map") is True


class TestLoading:
    def test_the_context_root_file_is_read(self, tmp_path: Path) -> None:
        (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")

        matcher = load_dockerignore(tmp_path, dockerfile)

        assert matcher.excludes("app.log") is True

    def test_a_dockerfile_specific_file_wins(self, tmp_path: Path) -> None:
        """A repository holding several Dockerfiles gives each its own exclusions."""
        (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        dockerfile = tmp_path / "api.Dockerfile"
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / "api.Dockerfile.dockerignore").write_text(
            "*.tmp\n", encoding="utf-8"
        )

        matcher = load_dockerignore(tmp_path, dockerfile)

        assert matcher.excludes("scratch.tmp") is True
        assert matcher.excludes("app.log") is False

    def test_no_file_at_all_excludes_nothing(self, tmp_path: Path) -> None:
        matcher = load_dockerignore(tmp_path, tmp_path / "Dockerfile")

        assert bool(matcher) is False
