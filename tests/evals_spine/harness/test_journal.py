# module-kind: tests
"""A cell that was paid for survives the process that produced it.

Driven through the recursion-depth binding rather than a synthetic one, because
what makes the mechanism worth having is that a real record model round-trips
through it: these models carry ``computed_field`` totals and forbid extras, and
the two halves of that disagree by default.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.errors import HarnessJournalMismatchError, HarnessJournalUnwritableError
from evals.harness.journal import (
    RecordedCells,
    ResumeState,
    RunJournal,
    open_journal,
)
from evals.recursion_depth.journal import (
    JOURNAL_KIND,
    JOURNAL_NAME,
    PROGRESS_NAME,
    PROGRESS_SPEC,
    SPEC,
    cell_key,
    matrix_identity,
    open_cell_journal,
    progress_by_cell,
    sessions_spent,
)
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    LEAF,
    PLAN,
    CellProgressRecord,
    CellRecord,
    PlannedTreeRecord,
    Provenance,
    UnitRecord,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_EXECUTOR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)
_REVIEWER = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-expert-001"),
    capability="expert",
    family=NotBlankStr("example-family-b"),
)


def _provenance(*, commit: str = "0" * 40) -> Provenance:
    """Build a provenance stamp.

    Returns:
        The provenance.
    """
    return Provenance(
        generated_at=datetime(2026, 8, 22, tzinfo=UTC),
        git_commit=NotBlankStr(commit),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
        spec_id=NotBlankStr("tiny"),
        requirement_count=2,
        executor=_EXECUTOR,
        reviewer=_REVIEWER,
        independence=Independence.CROSS_FAMILY,
    )


def _measured(
    *, depth_cap: int = 1, arm: Arm = Arm.GATED, tokens: int = 10
) -> CellRecord:
    """Build a measured cell carrying one leaf.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=depth_cap,
        arm=arm,
        repetition=0,
        achieved_depth=1,
        units=(
            UnitRecord(
                unit_id=NotBlankStr("leaf-1"),
                title=NotBlankStr("A leaf"),
                kind=LEAF,
                depth=1,
                delivered=True,
                attempts=2,
                turns=3,
                tokens=tokens,
            ),
        ),
    )


def _unavailable(*, depth_cap: int = 1, arm: Arm = Arm.UNGATED) -> CellRecord:
    """Build a cell that could not be measured.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=depth_cap,
        arm=arm,
        repetition=0,
        unavailable_reason="DecompositionError: the planner call failed",
    )


def _opened(
    tmp_path: Path, *, resume: bool, commit: str = "0" * 40
) -> tuple[RunJournal[CellRecord], ResumeState[CellRecord]]:
    """Open a journal at *tmp_path* under the recursion-depth binding.

    Returns:
        The journal and what a previous attempt paid for.
    """
    return open_journal(
        tmp_path,
        SPEC,
        identity=matrix_identity(_provenance(commit=commit)),
        resume=resume,
    )


def _plan_row(*, depth_cap: int = 1, arm: Arm = Arm.GATED) -> CellProgressRecord:
    """Build the planning session of one cell, carrying its tree.

    Returns:
        The progress row.
    """
    root = _root_task()
    return CellProgressRecord(
        depth_cap=depth_cap,
        arm=arm,
        repetition=0,
        unit=UnitRecord(
            unit_id=NotBlankStr("plan-1"),
            title=NotBlankStr("Plan: a tiny spec"),
            kind=PLAN,
            depth=0,
            attempts=1,
            cost=1.0,
        ),
        plan=PlannedTreeRecord(root=root, result=_tree(root)),
    )


def _leaf_row(
    *,
    depth_cap: int = 1,
    arm: Arm = Arm.GATED,
    unit_id: str = "leaf-1",
    attempts: int = 2,
) -> CellProgressRecord:
    """Build one built-leaf session of a cell.

    Returns:
        The progress row.
    """
    return CellProgressRecord(
        depth_cap=depth_cap,
        arm=arm,
        repetition=0,
        unit=UnitRecord(
            unit_id=NotBlankStr(unit_id),
            title=NotBlankStr("A leaf"),
            kind=LEAF,
            depth=1,
            delivered=True,
            attempts=attempts,
            turns=3,
            tokens=10,
        ),
    )


def _task(title: str) -> Task:
    """Build a task the harness could brief.

    Returns:
        The task.
    """
    return Task(
        # Derived rather than minted: this id is cross-referenced (the tree's
        # subtask names its child by it, and the plan names the root by it),
        # and the round-trip assertions read it back off the journal.
        id=as_uuid(f"task:{title}"),
        title=NotBlankStr(title),
        description=NotBlankStr(f"Do {title}."),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr(sid("project:recursion-depth-suite")),
        created_by=NotBlankStr("lead"),
    )


def _root_task() -> Task:
    """Build the objective a tree hangs off.

    Returns:
        The task.
    """
    return _task("Deliver the tiny spec")


def _tree(root: Task) -> DecompositionResult:
    """Build a one-level decomposition of *root*.

    Returns:
        The tree.
    """
    child = _task("Build the tiny thing")
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=NotBlankStr(str(root.id)),
            task_structure=TaskStructure.SEQUENTIAL,
            subtasks=(
                SubtaskDefinition(
                    # A subtask id IS its child task's id, in canonical UUID
                    # form: the result model refuses a level where the two
                    # sets differ.
                    id=NotBlankStr(str(child.id)),
                    title=NotBlankStr("Build the tiny thing"),
                    description=NotBlankStr("Build it."),
                    expected_artifacts=(NotBlankStr("tiny/thing.py"),),
                    satisfies=(NotBlankStr("R01"),),
                ),
            ),
        ),
        created_tasks=(child,),
    )


def _progress_opened(
    tmp_path: Path, *, resume: bool, commit: str = "0" * 40
) -> tuple[RunJournal[CellProgressRecord], ResumeState[CellProgressRecord]]:
    """Open the session journal at *tmp_path* under the same binding.

    Returns:
        The journal and every session previous attempts recorded.
    """
    return open_journal(
        tmp_path,
        PROGRESS_SPEC,
        identity=matrix_identity(_provenance(commit=commit)),
        resume=resume,
    )


class TestACellSurvivesTheProcess:
    """The failure this exists for is the process dying mid-matrix."""

    def test_a_recorded_cell_is_readable_before_the_sweep_ends(
        self, tmp_path: Path
    ) -> None:
        # Not on close, not on the report: a sweep killed after cell one must
        # have cell one. Seven hours of a live run were lost to the opposite.
        journal, _ = _opened(tmp_path, resume=False)

        journal.record(_measured())

        lines = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["depth_cap"] == 1

    def test_the_first_line_is_a_header_not_a_cell(self, tmp_path: Path) -> None:
        _opened(tmp_path, resume=False)

        first = json.loads((tmp_path / JOURNAL_NAME).read_text(encoding="utf-8"))
        assert first["journal_kind"] == JOURNAL_KIND
        assert first["git_commit"] == "0" * 40


class TestResume:
    """What a resume buys again, and what it refuses to."""

    def test_a_measured_cell_is_read_back(self, tmp_path: Path) -> None:
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_measured())
        journal.close()

        _, state = _opened(tmp_path, resume=True)

        held = state.holds(cell_key(1, Arm.GATED, 0))
        assert held is not None
        assert held.total_tokens == 10

    def test_an_unavailable_cell_is_attempted_again(self, tmp_path: Path) -> None:
        # It cost almost nothing, and the operator restarting has usually just
        # fixed the reason it failed. Reading it back hands them the same
        # broken report they restarted to escape.
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_unavailable())
        journal.close()

        _, state = _opened(tmp_path, resume=True)

        assert state.holds(cell_key(1, Arm.UNGATED, 0)) is None

    def test_a_journal_from_a_different_commit_is_refused(self, tmp_path: Path) -> None:
        # Cells measured before a change to the recursion point are cells about
        # a different system, and two of those are not one curve.
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_measured())
        journal.close()

        with pytest.raises(HarnessJournalMismatchError, match="git_commit"):
            _opened(tmp_path, resume=True, commit="1" * 40)

    def test_an_existing_journal_is_never_silently_overwritten(
        self, tmp_path: Path
    ) -> None:
        # Truncating it would discard hours of paid work, and this is not the
        # place that decision gets made.
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_measured())
        journal.close()

        with pytest.raises(HarnessJournalMismatchError, match="already exists"):
            _opened(tmp_path, resume=False)


class TestASessionSurvivesTheCellThatRanIt:
    """A cell is hours; a session is what a killed cell can still leave."""

    def test_a_session_is_readable_before_its_cell_ends(self, tmp_path: Path) -> None:
        # The whole point of the finer journal: a cell killed at hour six used
        # to leave nothing, because its record is written once, at the end.
        journal, _ = _progress_opened(tmp_path, resume=False)

        journal.record(_plan_row())
        journal.record(_leaf_row())

        lines = (tmp_path / PROGRESS_NAME).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_the_planning_row_carries_the_tree_the_units_belong_to(
        self, tmp_path: Path
    ) -> None:
        # Without it a resume holds directories nothing indexes, and every
        # ``parent_task_id`` names an objective that was minted per call.
        journal, _ = _progress_opened(tmp_path, resume=False)
        journal.record(_plan_row())
        journal.record(_leaf_row())
        journal.close()

        _, state = _progress_opened(tmp_path, resume=True)

        resumed = progress_by_cell(state)[cell_key(1, Arm.GATED, 0)]
        assert resumed.plan is not None
        # The root comes back as it was written, not re-minted: every
        # ``parent_task_id`` in the tree names it by id, so a fresh one would
        # leave the whole plan pointing at a task that does not exist.
        assert resumed.plan.root.id == _root_task().id
        assert resumed.plan.result.plan.parent_task_id == str(resumed.plan.root.id)
        assert [unit.unit_id for unit in resumed.units] == ["plan-1", "leaf-1"]

    def test_sessions_are_re_booked_from_the_rows_that_ran_them(
        self, tmp_path: Path
    ) -> None:
        # Otherwise a sweep resumed four times is bounded like four sweeps, and
        # the ceiling stops meaning what the manifest says it means.
        journal, _ = _progress_opened(tmp_path, resume=False)
        journal.record(_plan_row())
        journal.record(_leaf_row())
        journal.record(_leaf_row(arm=Arm.UNGATED, attempts=3))
        journal.close()

        _, state = _progress_opened(tmp_path, resume=True)

        assert sessions_spent(state) == 6

    def test_a_dead_cells_sessions_are_re_booked_too(self, tmp_path: Path) -> None:
        # The cell never finished, so it has no cell record at all, and every
        # session it burned is gone from the account either way. Reading spend
        # off the finished cells alone is how a sweep that keeps dying and
        # resuming outspends its own manifest.
        journal, _ = _progress_opened(tmp_path, resume=False)
        journal.record(_plan_row())
        journal.record(_leaf_row())
        journal.close()

        cells, cell_state = _opened(tmp_path, resume=False)
        cells.close()
        _, state = _progress_opened(tmp_path, resume=True)

        assert not cell_state.completed
        assert sessions_spent(state) == 3

    def test_a_second_plan_supersedes_the_units_of_the_first(
        self, tmp_path: Path
    ) -> None:
        # A cell whose trees were cleaned away starts again, and the units it
        # recorded against the old tree belong to nothing this attempt builds.
        # Continuing from a mix of the two would hand a merge one attempt's
        # directories under another attempt's plan.
        journal, _ = _progress_opened(tmp_path, resume=False)
        journal.record(_plan_row())
        journal.record(_leaf_row(unit_id="leaf-old"))
        journal.record(_plan_row())
        journal.record(_leaf_row(unit_id="leaf-new"))
        journal.close()

        _, state = _progress_opened(tmp_path, resume=True)

        resumed = progress_by_cell(state)[cell_key(1, Arm.GATED, 0)]
        assert [unit.unit_id for unit in resumed.units] == ["plan-1", "leaf-new"]
        # Both attempts were paid for, so both stay in the spend.
        assert sessions_spent(state) == 6

    def test_a_tree_on_a_row_that_did_no_planning_is_refused(self) -> None:
        # Two trees on one cell is a resume with a choice to make and nothing
        # to make it with.
        with pytest.raises(ValueError, match="only the planning row"):
            CellProgressRecord(
                depth_cap=1,
                arm=Arm.GATED,
                repetition=0,
                unit=_leaf_row().unit,
                plan=_plan_row().plan,
            )


class TestACrashMidWrite:
    """A truncated last line IS the crash this journal exists for."""

    def test_a_half_written_last_cell_is_dropped_not_fatal(
        self, tmp_path: Path
    ) -> None:
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_measured())
        journal.close()
        path = tmp_path / JOURNAL_NAME
        path.write_text(
            path.read_text(encoding="utf-8") + '{"depth_cap": 2, "ar',
            encoding="utf-8",
            newline="",
        )

        _, state = _opened(tmp_path, resume=True)

        assert len(state.completed) == 1

    def test_an_empty_journal_file_is_given_a_header_before_anything_lands(
        self, tmp_path: Path
    ) -> None:
        # A file whose header never reached the disk attributes nothing.
        # Appending under no header makes every cell in it unreadable at the
        # next resume, which is this file's own failure arrived at backwards.
        (tmp_path / JOURNAL_NAME).write_text("", encoding="utf-8", newline="")

        journal, state = _opened(tmp_path, resume=True)
        journal.record(_measured())
        journal.close()

        assert not state.completed
        _, resumed = _opened(tmp_path, resume=True)
        assert len(resumed.completed) == 1

    def test_a_broken_line_in_the_middle_is_corruption_and_refused(
        self, tmp_path: Path
    ) -> None:
        # Reading past it would silently drop a measured cell already paid for.
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_measured())
        journal.record(_measured(arm=Arm.UNGATED))
        journal.close()
        path = tmp_path / JOURNAL_NAME
        header, first, second = path.read_text(encoding="utf-8").splitlines()
        del first
        path.write_text(
            f"{header}\n{{'not': 'json'}}\n{second}\n", encoding="utf-8", newline=""
        )

        with pytest.raises(HarnessJournalMismatchError, match="corruption"):
            _opened(tmp_path, resume=True)


class TestOneJournalPerHarness:
    """Two harnesses write journals; neither may read the other's."""

    def test_a_journal_from_another_harness_is_refused_by_kind(
        self, tmp_path: Path
    ) -> None:
        # The identity fields would not even overlap, so without the kind the
        # refusal would be about the wrong thing or would not happen at all.
        journal, _ = _opened(tmp_path, resume=False)
        journal.close()
        path = tmp_path / JOURNAL_NAME
        header, *rest = path.read_text(encoding="utf-8").splitlines()
        foreign = json.loads(header) | {"journal_kind": "loop-ab"}
        path.write_text(
            "\n".join([json.dumps(foreign), *rest]) + "\n",
            encoding="utf-8",
            newline="",
        )

        with pytest.raises(HarnessJournalMismatchError, match="recursion-depth"):
            _opened(tmp_path, resume=True)


class TestTheSweepsOwnEntryPoint:
    """What the runner actually calls, wired end to end."""

    def test_it_hands_back_a_sink_that_journals(self, tmp_path: Path) -> None:
        cells, state = open_cell_journal(
            tmp_path, provenance=_provenance(), resume=False
        )

        cells.add(_measured())
        cells.close()

        assert not state.completed
        _, resumed = open_cell_journal(tmp_path, provenance=_provenance(), resume=True)
        assert len(resumed.completed) == 1


class TestRecordingIsOneOwner:
    """Remembering a cell and writing it down are never separable."""

    def test_a_cell_the_journal_refuses_is_not_remembered_either(
        self, tmp_path: Path
    ) -> None:
        # Otherwise the assembled report claims a cell the journal cannot
        # show, and the journal is what an operator reads after a run they
        # could not watch.
        journal, _ = _opened(tmp_path, resume=False)
        journal.close()
        cells = RecordedCells(journal, SPEC)

        with pytest.raises(HarnessJournalUnwritableError):
            cells.add(_measured())

        assert len(cells) == 0

    def test_an_unwritable_journal_raises_its_own_type(self, tmp_path: Path) -> None:
        # Typed, not the filesystem's own error: a driver's per-cell handler
        # must not read this as one cell's outcome and then try to write that
        # outcome to the same broken file.
        journal, _ = _opened(tmp_path, resume=False)
        journal.close()

        with pytest.raises(HarnessJournalUnwritableError, match="no longer keep"):
            journal.record(_measured())

    def test_adding_a_cell_journals_it(self, tmp_path: Path) -> None:
        # Four branches record a cell. A journal call beside each append is
        # four chances to add a fifth that only remembers.
        journal, _ = _opened(tmp_path, resume=False)
        cells = RecordedCells(journal, SPEC)

        cells.add(_measured())

        assert len(cells) == 1
        written = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        assert len(written) == 2

    def test_a_replayed_cell_is_not_written_twice(self, tmp_path: Path) -> None:
        journal, _ = _opened(tmp_path, resume=False)
        journal.record(_measured())
        cells = RecordedCells(journal, SPEC)

        cells.replay(_measured())

        assert len(cells) == 1
        written = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        assert len(written) == 2
