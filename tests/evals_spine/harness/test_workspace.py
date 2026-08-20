# module-kind: tests
"""The generic half of workspace provisioning.

Every harness on the recording spine seeds its runs the same way, and only the
key naming the tree differs. What is asserted here is the part that does not
know what a run is: the reset, the isolation between keys, and the containment
guard on a key that arrives from outside.
"""

from pathlib import Path

import pytest

from evals.errors import WorkspacePathEscapeError, WorkspaceSeedNotFoundError
from evals.harness.workspace import CellWorkspace, _contained, seed_workspace
from evals.runner.execution import EVAL_TASK_PROJECT

pytestmark = pytest.mark.unit

_SEED_DIR = "seeds/widget"


@pytest.fixture
def suite_root(tmp_path: Path) -> Path:
    """A suite root holding one committed seed fixture.

    Returns:
        The suite root.
    """
    seed = tmp_path / "suite" / _SEED_DIR
    (seed / "pkg").mkdir(parents=True)
    (seed / "README.md").write_text("seed readme\n", encoding="utf-8")
    (seed / "pkg" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path / "suite"


def _seed(cell_key: str, suite_root: Path, work_root: Path) -> CellWorkspace:
    """Provision one cell's workspace.

    Returns:
        The provisioned workspace.
    """
    return seed_workspace(
        cell_key=cell_key,
        seed_dir=_SEED_DIR,
        suite_root=suite_root,
        work_root=work_root,
    )


def test_the_seed_lands_in_the_project_subtree(
    suite_root: Path, tmp_path: Path
) -> None:
    # Both sandboxes a run drives resolve their mount through the project id, so
    # a flat workspace is one neither can bind.
    cell = _seed("unit-a", suite_root, tmp_path / "work")

    assert cell.project_dir == cell.root / "projects" / EVAL_TASK_PROJECT
    assert (cell.project_dir / "pkg" / "widget.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"


def test_a_repeat_cannot_inherit_the_previous_run(
    suite_root: Path, tmp_path: Path
) -> None:
    # The fair-comparison invariant every recorded artifact rests on: a run that
    # could inherit another's output would be graded on run order.
    work_root = tmp_path / "work"
    first = _seed("unit-a", suite_root, work_root)
    (first.project_dir / "leftover.txt").write_text("previous\n", encoding="utf-8")
    # Outside the project subtree, so only a reset of the whole sandbox root
    # clears it; a run can write here through the mount.
    (first.root / "stray.txt").write_text("outside\n", encoding="utf-8")

    second = _seed("unit-a", suite_root, work_root)

    assert second == first
    assert not (second.project_dir / "leftover.txt").exists()
    assert not (second.root / "stray.txt").exists()


def test_two_keys_are_isolated_under_one_work_root(
    suite_root: Path, tmp_path: Path
) -> None:
    work_root = tmp_path / "work"

    first = _seed("unit-a", suite_root, work_root)
    second = _seed("unit-b", suite_root, work_root)

    assert first != second
    assert isinstance(first, CellWorkspace)


def test_a_missing_seed_fixture_fails_loud(tmp_path: Path) -> None:
    (tmp_path / "suite").mkdir()

    with pytest.raises(WorkspaceSeedNotFoundError, match=_SEED_DIR):
        _seed("unit-a", tmp_path / "suite", tmp_path / "work")


def test_a_cell_key_that_escapes_the_work_root_is_refused(
    suite_root: Path, tmp_path: Path
) -> None:
    # A recursion harness keys its trees on ids a planning agent authored, so
    # the guard is load-bearing here rather than a second line behind a model
    # boundary as it is for a brief id.
    with pytest.raises(WorkspacePathEscapeError):
        _seed("../escaped", suite_root, tmp_path / "work")


def test_the_containment_guard_admits_a_contained_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    assert _contained(Path("inside") / "deeper", root) == (
        root.resolve() / "inside" / "deeper"
    )
