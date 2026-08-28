# module-kind: tests
"""A merge is judged on the tree it assembled, never on its own paperwork.

The regression these pin cost a matrix. A merge declared its report and its
end-to-end output as its expected artifacts, and delivery was decided by asking
the shared artifact probe about those, so a merge that assembled the whole
package and skipped one markdown file was recorded as having changed nothing.

That verdict does not stay local. ``merge_brief`` marks a child
``[DID NOT DELIVER]`` for its parent, so the false negative is briefed upward,
and it can only fire BELOW the root: a cap-1 tree has no intermediate merges to
mislabel and scored 35 to 38 of 42, while a cap-2 tree told its root that four
of seven subtrees had failed scored zero. A defect that only fires with depth
reads exactly like depth not working.
"""

from datetime import date
from pathlib import Path

import pytest

from evals.harness.workspace import CellWorkspace
from evals.recursion_depth.merge import (
    CHILDREN_DIR,
    MERGE_REPORT_PATH,
    MergePiece,
    MergePlan,
    assembled_tree,
    merge_brief,
)
from evals.recursion_depth.session import SessionLimits
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


def _workspace(tmp_path: Path) -> CellWorkspace:
    """Build a merge workspace with its project directory present.

    Returns:
        The workspace.
    """
    workspace = CellWorkspace(root=tmp_path / "merge")
    workspace.project_dir.mkdir(parents=True, exist_ok=True)
    return workspace


def _seed_child(workspace: CellWorkspace, slug: str) -> None:
    """Mount one child the way the merge loop does, before any attempt runs."""
    child = workspace.project_dir / CHILDREN_DIR / slug / "sqlcsv"
    child.mkdir(parents=True, exist_ok=True)
    (child / "lexer.py").write_text("# a child's work\n", encoding="utf-8")


class TestWhatCountsAsAssembly:
    """The fingerprint answers what THIS merge produced."""

    def test_a_mounted_child_is_not_an_assembly(self, tmp_path: Path) -> None:
        """Otherwise every merge reads as delivered before it runs."""
        workspace = _workspace(tmp_path)
        _seed_child(workspace, "00-lexer")

        assert assembled_tree(workspace) == frozenset()

    def test_the_brief_and_the_paperwork_are_not_an_assembly(
        self, tmp_path: Path
    ) -> None:
        """A merge that only wrote its report assembled nothing."""
        workspace = _workspace(tmp_path)
        _seed_child(workspace, "00-lexer")
        (workspace.project_dir / "README.md").write_text("brief", encoding="utf-8")
        report = workspace.project_dir / MERGE_REPORT_PATH
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("I assembled it", encoding="utf-8")

        assert assembled_tree(workspace) == frozenset()

    def test_a_package_at_the_root_is_an_assembly(self, tmp_path: Path) -> None:
        """The deliverable is the tree at the workspace root."""
        workspace = _workspace(tmp_path)
        _seed_child(workspace, "00-lexer")
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        written = package / "lexer.py"
        written.write_text("# assembled\n", encoding="utf-8")

        # Size read back rather than assumed: the platform may translate line
        # endings on the way out, and this pins the path, not the encoding.
        assert assembled_tree(workspace) == frozenset(
            {("sqlcsv/lexer.py", written.stat().st_size)}
        )

    def test_assembling_without_the_report_still_counts(self, tmp_path: Path) -> None:
        """The defect this exists for, stated as a test.

        Judged on the declared paths, this merge produced nothing and its
        parent was told so.
        """
        workspace = _workspace(tmp_path)
        _seed_child(workspace, "00-lexer")
        before = assembled_tree(workspace)
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        (package / "lexer.py").write_text("# assembled\n", encoding="utf-8")

        assert assembled_tree(workspace) != before

    def test_an_edit_to_an_assembled_file_counts(self, tmp_path: Path) -> None:
        """A repair round that rewrites the assembly has changed the tree."""
        workspace = _workspace(tmp_path)
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        target = package / "lexer.py"
        target.write_text("# first\n", encoding="utf-8")
        before = assembled_tree(workspace)
        target.write_text("# rewritten, and longer\n", encoding="utf-8")

        assert assembled_tree(workspace) != before


class TestWhatTheParentIsTold:
    """The delivery verdict travels upward, so it has to be right."""

    def test_a_delivering_child_is_not_marked(self) -> None:
        piece = MergePiece(
            title="SQL lexer", slug="00-lexer", tree=Path("tree"), delivered=True
        )

        assert "[DID NOT DELIVER]" not in merge_brief(_plan_with(piece), ())

    def test_a_child_that_delivered_nothing_is_marked(self) -> None:
        """The signal is worth keeping; it just has to be true."""
        piece = MergePiece(
            title="SQL lexer", slug="00-lexer", tree=Path("tree"), delivered=False
        )

        assert "[DID NOT DELIVER]" in merge_brief(_plan_with(piece), ())


def _plan_with(piece: MergePiece) -> MergePlan:
    """Build the smallest plan carrying *piece*.

    Returns:
        The plan.
    """
    return MergePlan(
        task=Task(
            id=as_uuid("task:assemble"),
            title=NotBlankStr("Assemble it"),
            description=NotBlankStr("Assemble the pieces."),
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project=NotBlankStr(sid("project:merge-delivery")),
            created_by=NotBlankStr("test"),
        ),
        owner=AgentIdentity(
            id=as_uuid("identity:assembler"),
            name=NotBlankStr("Assembler"),
            role=NotBlankStr("Developer"),
            department=NotBlankStr("Engineering"),
            model=ModelConfig(
                provider=NotBlankStr("example-provider"),
                model_id=NotBlankStr("example-capable-001"),
                capability="capable",
            ),
            hiring_date=date(2026, 1, 1),
        ),
        workspace=CellWorkspace(root=Path("unused")),
        pieces=(piece,),
        criteria=(NotBlankStr("It runs"),),
        execution_prefix="d2-gated-r0-merge",
        limits=SessionLimits(max_turns=4, cost_ceiling=1.0, token_ceiling=1000),
        attempts=2,
    )
