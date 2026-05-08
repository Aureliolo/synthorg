"""Unit tests for ``scripts/check_workflow_tag_lifecycle.py``.

Loads the script as a module so its private helpers are callable
without spawning subprocesses.

Covers:

* Positive matches: ``gh api -X DELETE``, ``gh api --method DELETE``,
  ``gh release delete --cleanup-tag``, multi-line line continuations
  on both create and delete sides, and reversed ``-f ref=`` / endpoint
  argument order.
* Negative cases: heads-ref creates (``refs/heads/...``), pure
  create-only or delete-only workflows, full-line shell comments that
  contain delete-shaped strings.
* Per-line opt-out (``# lint-allow: workflow-tag-lifecycle --
  <reason>``) with mandatory non-empty justification.
* The ``_SHELL_COMMENT_RE`` line-number-preservation fix (using
  ``[ \t]*`` instead of ``\\s*`` so blank lines preceding comment
  blocks don't get coalesced).
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_workflow_tag_lifecycle.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_workflow_tag_lifecycle",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _scan(tmp_path: Path, content: str) -> tuple[list[int], list[int]]:
    """Write content to a tmp .yml file and return ``(creates, deletes)``."""
    target = tmp_path / "wf.yml"
    target.write_text(content, encoding="utf-8")
    creates, deletes = _MODULE._scan_file(target)  # type: ignore[attr-defined]
    return creates, deletes


# --------------------------------------------------------------------------- #
# Positive create matches
# --------------------------------------------------------------------------- #


class TestCreateMatching:
    """The CREATE regex catches tag-create shapes."""

    def test_single_line_create(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: gh api repos/foo/bar/git/refs -f ref=refs/tags/v1\n"
        )
        creates, deletes = _scan(tmp_path, content)
        assert creates == [4]
        assert deletes == []

    def test_multi_line_create_continuation(self, tmp_path: Path) -> None:
        """Backslash-newline continuations between args don't bypass."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            '          gh api "repos/foo/bar/git/refs" \\\n'
            '            -f ref="refs/tags/v1" \\\n'
            '            -f sha="$SHA"\n'
        )
        creates, deletes = _scan(tmp_path, content)
        assert len(creates) == 1
        assert deletes == []

    def test_reversed_argument_order(self, tmp_path: Path) -> None:
        """``-f ref=`` BEFORE the endpoint still hits the same POST."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            '          gh api -f ref="refs/tags/v1" \\\n'
            '            "repos/foo/bar/git/refs"\n'
        )
        creates, _deletes = _scan(tmp_path, content)
        assert len(creates) == 1


class TestCreateNonMatches:
    """The CREATE regex doesn't trip on look-alikes."""

    def test_heads_ref_create_is_not_a_tag_create(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: gh api repos/foo/bar/git/refs -f ref=refs/heads/main\n"
        )
        creates, deletes = _scan(tmp_path, content)
        assert creates == []
        assert deletes == []

    def test_path_segment_not_endpoint(self, tmp_path: Path) -> None:
        """``git/refs/heads/...`` is not the create endpoint."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            '      - run: echo "git/refs/tags/v1 is just a string"\n'
        )
        creates, _deletes = _scan(tmp_path, content)
        assert creates == []


# --------------------------------------------------------------------------- #
# Positive delete matches
# --------------------------------------------------------------------------- #


class TestDeleteMatching:
    """The DELETE regex catches every tag-delete shape."""

    def test_gh_api_x_delete_single_line(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            '      - run: gh api -X DELETE "repos/foo/bar/git/refs/tags/v1"\n'
        )
        _creates, deletes = _scan(tmp_path, content)
        assert deletes == [4]

    def test_gh_api_method_delete(self, tmp_path: Path) -> None:
        """``--method DELETE`` is semantically identical to ``-X DELETE``."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            '      - run: gh api --method DELETE "repos/foo/bar/git/refs/tags/v1"\n'
        )
        _creates, deletes = _scan(tmp_path, content)
        assert len(deletes) == 1

    def test_gh_release_delete_cleanup_tag(self, tmp_path: Path) -> None:
        """``gh release delete --cleanup-tag`` deletes both release + tag."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: gh release delete v1 --yes --cleanup-tag\n"
        )
        _creates, deletes = _scan(tmp_path, content)
        assert len(deletes) == 1

    def test_multi_line_delete_continuation(self, tmp_path: Path) -> None:
        """Backslash-newline continuations between args don't bypass."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            "          gh api \\\n"
            "            -X DELETE \\\n"
            '            "repos/foo/bar/git/refs/tags/v1"\n'
        )
        _creates, deletes = _scan(tmp_path, content)
        assert len(deletes) == 1


class TestDeleteNonMatches:
    """The DELETE regex doesn't trip on look-alikes."""

    def test_release_delete_without_cleanup_tag(self, tmp_path: Path) -> None:
        """Release-only delete (no ``--cleanup-tag``) leaves the tag alone."""
        content = "jobs:\n  x:\n    steps:\n      - run: gh release delete v1 --yes\n"
        _creates, deletes = _scan(tmp_path, content)
        assert deletes == []


# --------------------------------------------------------------------------- #
# Combined CREATE + DELETE reports both
# --------------------------------------------------------------------------- #


class TestCombined:
    """When both shapes are present in the same file, both line lists fill."""

    def test_create_and_delete_both_reported(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            "          gh api repos/foo/bar/git/refs -f ref=refs/tags/v1\n"
            '          gh api -X DELETE "repos/foo/bar/git/refs/tags/v1"\n'
        )
        creates, deletes = _scan(tmp_path, content)
        assert len(creates) == 1
        assert len(deletes) == 1


# --------------------------------------------------------------------------- #
# Shell-comment scrubbing (regression: line-number preservation)
# --------------------------------------------------------------------------- #


class TestShellCommentScrub:
    """Full-line shell comments are stripped without shifting line numbers."""

    def test_documented_example_in_comment_does_not_trip(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            "          # example: gh api -X DELETE git/refs/tags/foo\n"
            "          echo no-op\n"
        )
        _creates, deletes = _scan(tmp_path, content)
        assert deletes == []

    def test_blank_line_before_comment_block_preserves_line_numbers(
        self, tmp_path: Path
    ) -> None:
        """Regression: ``\\s*`` previously ate the blank-line newline."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            "\n"
            "          # comment block line 1\n"
            "          # comment block line 2\n"
            "          gh api repos/foo/bar/git/refs -f ref=refs/tags/v1\n"
        )
        creates, _deletes = _scan(tmp_path, content)
        # The create is on raw line 8; the line number reported must
        # match the source-of-truth line, not a coalesced offset.
        assert creates == [8]


# --------------------------------------------------------------------------- #
# Per-line opt-out
# --------------------------------------------------------------------------- #


class TestOptOut:
    """The ``# lint-allow: workflow-tag-lifecycle -- <reason>`` opt-out."""

    def test_opt_out_with_reason_suppresses_match(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: gh release delete v1 --yes --cleanup-tag  "
            "# lint-allow: workflow-tag-lifecycle -- bulk cleanup of old tags\n"
        )
        _creates, deletes = _scan(tmp_path, content)
        assert deletes == []

    def test_opt_out_without_reason_rejected(self, tmp_path: Path) -> None:
        """Whitespace-only reason after ``--`` doesn't suppress."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: gh release delete v1 --yes --cleanup-tag  "
            "# lint-allow: workflow-tag-lifecycle --   \n"
        )
        _creates, deletes = _scan(tmp_path, content)
        assert len(deletes) == 1

    def test_opt_out_for_unrelated_gate_does_not_suppress(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: gh release delete v1 --yes --cleanup-tag  "
            "# lint-allow: some-other-gate -- not this one\n"
        )
        _creates, deletes = _scan(tmp_path, content)
        assert len(deletes) == 1

    def test_opt_out_spans_multi_line_match(self, tmp_path: Path) -> None:
        """Marker on any line the match spans is enough."""
        content = (
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - run: |\n"
            "          gh api \\\n"
            "            -X DELETE \\\n"
            '            "repos/foo/bar/git/refs/tags/v1"  '
            "# lint-allow: workflow-tag-lifecycle -- documented exception\n"
        )
        _creates, deletes = _scan(tmp_path, content)
        assert deletes == []


# --------------------------------------------------------------------------- #
# Repo-level smoke: every shipped workflow passes the gate
# --------------------------------------------------------------------------- #


class TestRepoSmoke:
    """The repository's own workflows must pass the gate."""

    def test_all_repo_workflows_pass(self) -> None:
        workflows = list((_REPO_ROOT / ".github" / "workflows").rglob("*.yml")) + list(
            (_REPO_ROOT / ".github" / "workflows").rglob("*.yaml")
        )
        offenders: list[tuple[str, list[int], list[int]]] = []
        for wf in workflows:
            creates, deletes = _MODULE._scan_file(wf)  # type: ignore[attr-defined]
            if creates and deletes:
                offenders.append((wf.name, creates, deletes))
        assert offenders == [], f"Workflows with create+delete pattern: {offenders}"
