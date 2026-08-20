"""What the planner is told the project workspace holds.

The brief's foundation rule is unconditional, but a rule alone only stops the
planner asserting; it cannot tell it what is true. A live run planned a
brand-new project on seven filenames recalled from a different project, so the
brief carries the actual inventory too. It is stated in words that cannot be
read as "unknown": an absent workspace and an unlisted one look identical to a
planner, and only one of them means there is nothing there.
"""

from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.inventory import (
    MAX_LISTED_ENTRIES,
    describe_project_workspace,
)
from synthorg.engine.workspace.paths import project_workspace_dir

pytestmark = pytest.mark.unit


def _tree(root: Path, project_id: str) -> Path:
    path = project_workspace_dir(root, project_id)
    path.mkdir(parents=True)
    return path


class TestDescribeProjectWorkspace:
    async def test_a_missing_workspace_says_nothing_is_there(
        self, tmp_path: Path
    ) -> None:
        summary = await describe_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("never-ran")
        )

        assert "nothing" in summary.lower()

    async def test_an_empty_workspace_says_nothing_is_there(
        self, tmp_path: Path
    ) -> None:
        """Absent and empty are the same fact to a planner: no files exist."""
        _tree(tmp_path, "empty")

        summary = await describe_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("empty")
        )

        assert "nothing" in summary.lower()

    async def test_files_are_listed(self, tmp_path: Path) -> None:
        path = _tree(tmp_path, "proj")
        (path / "server.js").write_text("", encoding="utf-8")
        (path / "index.html").write_text("", encoding="utf-8")

        summary = await describe_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj")
        )

        assert "server.js" in summary
        assert "index.html" in summary

    async def test_directories_are_marked_as_such(self, tmp_path: Path) -> None:
        path = _tree(tmp_path, "proj")
        (path / "test").mkdir()

        summary = await describe_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj")
        )

        assert "test/" in summary

    async def test_the_git_directory_is_not_project_content(
        self, tmp_path: Path
    ) -> None:
        """Every workspace has one; naming it would read as work already done."""
        path = _tree(tmp_path, "proj")
        (path / ".git").mkdir()

        summary = await describe_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj")
        )

        assert ".git" not in summary
        assert "nothing" in summary.lower()

    async def test_a_large_tree_is_truncated_and_says_so(self, tmp_path: Path) -> None:
        """A brief is a prompt; an unbounded listing would crowd out the plan."""
        path = _tree(tmp_path, "proj")
        for index in range(MAX_LISTED_ENTRIES + 5):
            (path / f"file-{index:03d}.txt").write_text("", encoding="utf-8")

        summary = await describe_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj")
        )

        assert "5 more" in summary

    async def test_a_traversing_project_id_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="traversal"):
            await describe_project_workspace(
                base_root=tmp_path, project_id=NotBlankStr("../elsewhere")
            )
