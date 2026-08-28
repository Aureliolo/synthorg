# module-kind: tests
"""A unit is judged on the tree it produced, never on a guess made before it ran.

Two live regressions, one primitive. Both inferred delivery instead of asking,
and both were wrong in the same direction.

A MERGE declared its report and end-to-end output as its expected artifacts and
was judged on those, so one that assembled the whole package and skipped a
markdown file read as having changed nothing. That verdict is briefed to the
parent as ``[DID NOT DELIVER]``, and it can only fire BELOW the root: a cap-1
tree has no intermediate merges to mislabel and scored 35 to 38 of 42, while a
cap-2 tree told its root that four of seven subtrees had failed scored zero. A
defect that only fires with depth reads exactly like depth not working.

A LEAF was judged on whether any PLANNER-DECLARED path had changed, a guess made
before the tree existed. Measured on one cap-1 cell: two leaves, one of four
files and one of ten, both booked as having produced nothing because they had
named their modules differently. That feeds the survival denominator, so it
removed them from the metric rather than merely mislabelling them.
"""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from evals.harness.workspace import CellWorkspace
from evals.recursion_depth.merge import (
    CHILDREN_DIR,
    MERGE_REPORT_PATH,
    MergePiece,
    MergePlan,
    merge_brief,
)
from evals.recursion_depth.session import SessionLimits
from evals.recursion_depth.unit import UnitDelivery, produced_tree
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


def _digest(path: Path) -> str:
    """Digest *path* the way the fingerprint does.

    Returns:
        The hex digest of the file's bytes as written.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

        assert produced_tree(workspace) == frozenset()

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

        assert produced_tree(workspace) == frozenset()

    def test_a_package_at_the_root_is_an_assembly(self, tmp_path: Path) -> None:
        """The deliverable is the tree at the workspace root."""
        workspace = _workspace(tmp_path)
        _seed_child(workspace, "00-lexer")
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        written = package / "lexer.py"
        written.write_text("# assembled\n", encoding="utf-8")

        # Digest read back rather than assumed: the platform may translate
        # line endings on the way out, and this pins the path, not the bytes.
        assert produced_tree(workspace) == frozenset(
            {("sqlcsv/lexer.py", _digest(written))}
        )

    def test_an_edit_that_keeps_a_files_length_is_produced_work(
        self, tmp_path: Path
    ) -> None:
        """A size is blind to the edit that flips a constant.

        This harness has ONE delivery check where the product has two, so
        nothing else here would see it: the product compares the tree by size
        AND each declared path by digest, and the second is what catches this.
        """
        workspace = _workspace(tmp_path)
        target = workspace.project_dir / "sqlcsv" / "config.py"
        target.parent.mkdir(parents=True)
        target.write_text("RETRIES = 1\n", encoding="utf-8")
        before = produced_tree(workspace)
        target.write_text("RETRIES = 5\n", encoding="utf-8")

        assert produced_tree(workspace) != before

    def test_assembling_without_the_report_still_counts(self, tmp_path: Path) -> None:
        """The defect this exists for, stated as a test.

        Judged on the declared paths, this merge produced nothing and its
        parent was told so.
        """
        workspace = _workspace(tmp_path)
        _seed_child(workspace, "00-lexer")
        before = produced_tree(workspace)
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        (package / "lexer.py").write_text("# assembled\n", encoding="utf-8")

        assert produced_tree(workspace) != before

    def test_an_edit_to_an_assembled_file_counts(self, tmp_path: Path) -> None:
        """A repair round that rewrites the assembly has changed the tree."""
        workspace = _workspace(tmp_path)
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        target = package / "lexer.py"
        target.write_text("# first\n", encoding="utf-8")
        before = produced_tree(workspace)
        target.write_text("# rewritten, and longer\n", encoding="utf-8")

        assert produced_tree(workspace) != before


class TestALeafThatNamedItsFilesDifferently:
    """The leaf half of the same defect, and the one that reached a metric."""

    def test_code_under_an_undeclared_name_still_counts(self, tmp_path: Path) -> None:
        """Two live leaves, of four and ten files, were booked as producing
        nothing because the planner had guessed different module names."""
        workspace = _workspace(tmp_path)
        before = produced_tree(workspace)
        package = workspace.project_dir / "sqlcsv"
        package.mkdir()
        # The planner declared `csv_reader.py`; the leaf wrote `reader.py`.
        (package / "reader.py").write_text("# real work\n", encoding="utf-8")

        assert produced_tree(workspace) != before

    def test_a_leaf_that_wrote_only_its_report_produced_nothing(
        self, tmp_path: Path
    ) -> None:
        """Paperwork is not the deliverable at either level."""
        workspace = _workspace(tmp_path)
        report = workspace.project_dir / ".synthorg" / "unit" / "report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("I built it, honest", encoding="utf-8")

        assert produced_tree(workspace) == frozenset()


class TestWhatTheParentIsTold:
    """The delivery verdict travels upward, so it has to be right."""

    def test_a_clean_child_is_not_marked(self) -> None:
        piece = _piece(UnitDelivery(produced=True, reason=""))

        assert "[" not in merge_brief(_plan_with(piece), ()).split("The whole")[0]

    def test_a_child_that_built_nothing_says_so(self) -> None:
        """The signal is worth keeping; it just has to be true."""
        piece = _piece(UnitDelivery(produced=False, reason="it ran no turns"))

        assert "[BUILT NOTHING]" in merge_brief(_plan_with(piece), ())

    def test_a_child_that_built_but_failed_a_check_is_not_called_empty(self) -> None:
        """The defect, stated as a test.

        A sub-merge that assembled its whole package and left its children's
        tests under ``.children/`` collects no tests, because the grader runs
        pytest at the workspace root and pytest never descends into a
        dot-prefixed directory. Both facts used to arrive at the parent as
        ``[DID NOT DELIVER]``.

        Measured on a live cap-2 cell: four of the root's seven pieces were
        marked that way while holding 46, 46, 41 and 36 modules. The root was
        told most of its inputs had failed, wrote nothing across six attempts
        and 119 turns, and the cell scored 0 of 42.
        """
        piece = _piece(
            UnitDelivery(
                produced=True,
                reason="the merged tree's own tests did not pass: "
                "the suite collected no tests",
            )
        )

        brief = merge_brief(_plan_with(piece), ())

        assert "[BUILT NOTHING]" not in brief
        assert "BUILT, BUT NOT SIGNED OFF" in brief
        assert "the suite collected no tests" in brief

    def test_the_prose_does_not_contradict_the_marks(self) -> None:
        """One brief cannot say both things and be acted on.

        The prose asserted every piece had passed its own tests while the list
        beneath it marked four of seven as failures.
        """
        piece = _piece(UnitDelivery(produced=False, reason="it ran no turns"))

        brief = merge_brief(_plan_with(piece), ())

        assert "has passed its own tests" not in brief

    def test_the_merge_is_told_where_its_tests_have_to_live(self) -> None:
        """Its verdict depends on this and nothing used to say it."""
        brief = merge_brief(_plan_with(_piece(UnitDelivery(True, ""))), ())

        assert ".children" in brief
        assert "not searched" in brief


def _piece(delivery: UnitDelivery) -> MergePiece:
    """Build a piece carrying *delivery*.

    Returns:
        The piece.
    """
    return MergePiece(
        title="SQL lexer", slug="00-lexer", tree=Path("tree"), delivery=delivery
    )


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
