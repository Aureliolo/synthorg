"""What may become a project's workspace directory name.

This guard is the only thing between the deletion path and the root holding
every project's tree, so it is tested on what it REFUSES rather than on what it
builds. A separator check alone is not enough: pathlib drops a lone ``.``
component while parsing, so the join lands on the projects root itself, and a
segment carrying a drive discards everything to its left.
"""

from pathlib import Path

import pytest

from synthorg.engine.errors import WorkspaceSetupError
from synthorg.engine.workspace.paths import PROJECTS_SUBDIR, project_workspace_dir

pytestmark = pytest.mark.unit

_BASE = Path("/srv/workspaces")


class TestProjectWorkspaceDir:
    def test_a_plain_id_resolves_under_the_projects_root(self) -> None:
        resolved = project_workspace_dir(_BASE, "0ae08a07")

        assert resolved == _BASE / PROJECTS_SUBDIR / "0ae08a07"

    @pytest.mark.parametrize(
        "project_id",
        [
            "..",
            "../elsewhere",
            "a/b",
            "a\\b",
            pytest.param(".", id="dot-resolves-to-the-projects-root"),
            pytest.param("D:", id="drive-resets-the-whole-join"),
            pytest.param("C:evil", id="drive-relative"),
        ],
    )
    def test_anything_that_is_not_one_name_is_refused(self, project_id: str) -> None:
        with pytest.raises(WorkspaceSetupError):
            project_workspace_dir(_BASE, project_id)

    def test_the_projects_root_itself_is_never_the_answer(self) -> None:
        """The case that matters: this path is handed to a recursive delete.

        A guard that admits ``.`` returns the directory holding every
        project's tree, and the caller removes it.
        """
        projects_root = _BASE / PROJECTS_SUBDIR

        for project_id in (".", "..", "D:"):
            with pytest.raises(WorkspaceSetupError):
                assert project_workspace_dir(_BASE, project_id) != projects_root
