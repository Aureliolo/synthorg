"""The service must publish where the tree has got to, and never fail on it.

A recursive decomposition persists its tree once, at the end, so the plan it is
writing reads ``PLANNING`` with zero items for the whole run. A live run sat
there for 54 minutes with the backend log as the only way to tell a working
decomposition from a hung one.

The reporting seam is deliberately best-effort: a decomposition is minutes to
hours of real provider spend, so losing the line an operator watches costs a
refresh while failing the run over it costs the tree. Both halves are pinned
here, because a seam that reports and a seam that cannot break the run are two
different promises and only one of them is obvious from the call site.
"""

import pytest

from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._progress_publish import publish_progress
from synthorg.engine.decomposition._recursion import TreeSessionLedger
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.progress_protocol import (
    DecompositionProgressReporter,
)
from synthorg.engine.decomposition.service import DecompositionService
from tests._shared import FakeClock, as_uuid, sid
from tests.unit.engine.decomposition._doubles import Bounds, ScriptedStrategy
from tests.unit.engine.decomposition._doubles import (
    config_resolver as scripted_resolver,
)

pytestmark = pytest.mark.unit

#: Well clear of anything these cases plan, so no backstop is what is measured.
_A_GENEROUS_CEILING = 60.0
_MAX_ARTIFACTS = 1
_MAX_CRITERIA = 20
_MAX_DEPTH = 3
_MAX_SUBTASKS = 10
_MAX_TREE_SESSIONS = 40

_BOUNDS = Bounds(
    ceiling=_A_GENEROUS_CEILING,
    artifacts=_MAX_ARTIFACTS,
    criteria=_MAX_CRITERIA,
    depth=_MAX_DEPTH,
    subtasks=_MAX_SUBTASKS,
    tree_sessions=_MAX_TREE_SESSIONS,
)


class _RecordingReporter:
    """Remembers every snapshot it is handed."""

    def __init__(self) -> None:
        self.reports: list[tuple[str, DecompositionProgress]] = []

    async def report(
        self, *, objective_task_id: str, progress: DecompositionProgress
    ) -> None:
        """Record one snapshot."""
        self.reports.append((objective_task_id, progress))


class _BrokenReporter:
    """A reporter whose durable store is unreachable."""

    def __init__(self) -> None:
        self.calls = 0

    async def report(
        self, *, objective_task_id: str, progress: DecompositionProgress
    ) -> None:
        """Fail the way a repository write does.

        Raises:
            RuntimeError: Always.
        """
        del objective_task_id, progress
        self.calls += 1
        msg = "the plan row could not be written"
        raise RuntimeError(msg)


def _task(label: str) -> Task:
    """Build the objective a decomposition runs against.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(label),
        title=NotBlankStr(f"Objective {label}"),
        description=NotBlankStr("Deliver the thing"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-progress"),
        created_by=NotBlankStr("operator"),
        status=TaskStatus.CREATED,
    )


def _subtask(label: str) -> SubtaskDefinition:
    """Build one atomic subtask.

    Returns:
        The definition.
    """
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
    )


def _service(reporter: DecompositionProgressReporter) -> DecompositionService:
    """Build a service over a flat two-unit plan, reporting into *reporter*.

    Returns:
        The service.
    """
    plan = DecompositionPlan(
        parent_task_id=NotBlankStr(str(as_uuid("root"))),
        subtasks=(_subtask("one"), _subtask("two")),
        task_structure=TaskStructure.PARALLEL,
    )
    return DecompositionService(
        ScriptedStrategy({str(as_uuid("root")): plan}),
        TaskStructureClassifier(),
        config_resolver=scripted_resolver(_BOUNDS, recursion_enabled=True),
        progress_reporter=reporter,
    )


class TestTheTreeSaysWhereItIs:
    async def test_it_reports_before_the_first_level_lands(self) -> None:
        # The first level is a whole planning session, and that wait is
        # precisely when an operator has no other signal. Reporting only after
        # a level would leave the page blank for the whole of it.
        reporter = _RecordingReporter()

        await _service(reporter).decompose_task(_task("root"), DecompositionContext())

        assert reporter.reports
        first = reporter.reports[0][1]
        assert first.units_planned == 0
        assert first.sessions_limit == _MAX_TREE_SESSIONS

    async def test_the_last_report_names_what_was_written(self) -> None:
        reporter = _RecordingReporter()

        await _service(reporter).decompose_task(_task("root"), DecompositionContext())

        assert reporter.reports[-1][1].units_planned == 2

    async def test_every_report_names_the_objective_not_the_node(self) -> None:
        # Each level below the root is handed its own child task, so a report
        # naming the node would name a subtask, and the row that carries the
        # answer is the objective's plan.
        reporter = _RecordingReporter()

        await _service(reporter).decompose_task(_task("root"), DecompositionContext())

        assert {task_id for task_id, _ in reporter.reports} == {str(as_uuid("root"))}


class TestReportingCannotBreakTheRun:
    async def test_a_failing_reporter_leaves_the_tree_intact(self) -> None:
        # The reason this seam is guarded at all: a decomposition is minutes to
        # hours of real spend, and a plan-row write that cannot land must not
        # take it down.
        reporter = _BrokenReporter()

        result = await _service(reporter).decompose_task(
            _task("root"), DecompositionContext()
        )

        assert len(result.plan.subtasks) == 2
        assert reporter.calls > 0

    async def test_no_reporter_decomposes_normally(self) -> None:
        # The complement, so a deployment with nothing wired still plans.
        service = DecompositionService(
            ScriptedStrategy(
                {
                    str(as_uuid("root")): DecompositionPlan(
                        parent_task_id=NotBlankStr(str(as_uuid("root"))),
                        subtasks=(_subtask("one"),),
                        task_structure=TaskStructure.PARALLEL,
                    )
                }
            ),
            TaskStructureClassifier(),
            config_resolver=scripted_resolver(_BOUNDS, recursion_enabled=True),
        )

        result = await service.decompose_task(_task("root"), DecompositionContext())

        assert len(result.plan.subtasks) == 1


class TestSilentWhenThereIsNothingToAddress:
    """Asserted against ``publish_progress``, which owns the rule.

    The service delegates to it and holds no second copy of the condition, so
    testing the service's own wrapper would pin the delegation rather than the
    behaviour, and would only reach it through a private method.
    """

    async def test_a_ledger_naming_no_objective_reports_nothing(self) -> None:
        # A harness builds the ledger itself and never names an objective, so
        # a report would address a row that does not exist.
        reporter = _RecordingReporter()

        await publish_progress(
            TreeSessionLedger(remaining=1, limit=1),
            reporter=reporter,
            clock=FakeClock(),
        )

        assert reporter.reports == []

    async def test_a_named_ledger_does_report(self) -> None:
        # The complement, so the case above is silence for the stated reason
        # rather than silence because nothing publishes at all.
        reporter = _RecordingReporter()

        await publish_progress(
            TreeSessionLedger(
                remaining=1, limit=1, objective_task_id=str(as_uuid("root"))
            ),
            reporter=reporter,
            clock=FakeClock(),
        )

        assert [task_id for task_id, _ in reporter.reports] == [str(as_uuid("root"))]
