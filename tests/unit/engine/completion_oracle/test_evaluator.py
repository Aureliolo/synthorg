"""Unit tests for the Layer 1 build/test oracle (classifier + evaluator)."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.completion_oracle.build_test_models import (
    GroundingRequirement,
    OracleVerdict,
)
from synthorg.engine.completion_oracle.classifier import classify_grounding_requirement
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.observability.events.completion_oracle import BUILD_TEST_GATE_EVALUATED
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionPurpose,
    CodeExecutionRecord,
)
from synthorg.persistence.plan_protocol import PlanRepository
from tests._shared import FakeClock, as_pk, as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_CLOCK = FakeClock()
_PROJECT = sid("proj-1")

#: Virtual seconds between two records, so "the newest one decides" has an
#: ordering to decide from.
_RECORD_GAP = 1.0


def _task(
    *artifact_types: ArtifactType,
    task_type: TaskType = TaskType.DEVELOPMENT,
) -> Task:
    expected = tuple(
        ExpectedArtifact(type=t, path=f"path/{t.value}") for t in artifact_types
    )
    return Task(
        title="t",
        description="d",
        type=task_type,
        project="p",
        created_by="c",
        artifacts_expected=expected,
    )


def _record(
    *,
    passed: bool,
    task_id: str,
    purpose: CodeExecutionPurpose = CodeExecutionPurpose.TESTS,
    command: str = "pytest",
) -> CodeExecutionRecord:
    """Build one record, each later than the last.

    The clock advances per record because the oracle decides the pass/fail axis
    from the NEWEST one. Minted at a single frozen instant they all tie, the
    fake store's ordering has nothing to order by, and a test seeding a pass
    after a failure would prove only that the two arrived in that order.
    """
    _CLOCK.advance(_RECORD_GAP)
    return CodeExecutionRecord(
        task_id=task_id,
        execution_id="exec-1",
        project_id="p",
        purpose=purpose,
        command=command,
        returncode=0 if passed else 1,
        passed=passed,
        timed_out=False,
        executed_at=_CLOCK.now(),
    )


class _FakeRecords:
    """Minimal in-memory ``CodeExecutionRecordRepository`` for the oracle.

    Honours newest-first ordering, ``limit`` and ``offset`` as well as the
    purpose filter, because the oracle's whole pass/fail axis is "the newest
    record decides". A double that returned insertion order would let a test
    seed a passing row after a failing one and still watch the oracle read the
    failure, so every "latest run wins" assertion would hold for the wrong
    reason and keep holding if the ordering contract were dropped.
    """

    def __init__(
        self,
        records: tuple[CodeExecutionRecord, ...] = (),
        *,
        raises: bool = False,
    ) -> None:
        self._records = records
        self._raises = raises

    async def append(self, record: CodeExecutionRecord, /) -> None:
        raise NotImplementedError

    async def query(
        self,
        filter_spec: CodeExecutionFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CodeExecutionRecord, ...]:
        if self._raises:
            msg = "record store unavailable"
            raise RuntimeError(msg)
        matching = [
            record
            for record in self._records
            if filter_spec.purpose is None or record.purpose is filter_spec.purpose
        ]
        matching.sort(key=lambda record: record.executed_at, reverse=True)
        return tuple(matching[offset : offset + limit])

    async def purge_before(self, threshold: datetime, /) -> int:
        raise NotImplementedError


class TestClassifier:
    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            (ArtifactType.CODE, GroundingRequirement.REQUIRED),
            (ArtifactType.TESTS, GroundingRequirement.REQUIRED),
            (ArtifactType.DOCUMENTATION, GroundingRequirement.NOT_APPLICABLE),
        ],
    )
    def test_classification_by_declared_artifact(
        self, declared: ArtifactType, expected: GroundingRequirement
    ) -> None:
        # A non-code task type isolates the declared-artifact signal: a CODE /
        # TESTS artifact grounds even a design task, a doc artifact does not.
        task = _task(declared, task_type=TaskType.DESIGN)
        assert classify_grounding_requirement(task) is expected

    def test_development_type_without_artifacts_fails_closed(self) -> None:
        # A DEVELOPMENT task is a code task on its type alone; it must be
        # REQUIRED even with no declared CODE / TESTS artifact, so an agent
        # cannot dodge the build/test oracle by omitting the declaration.
        assert (
            classify_grounding_requirement(_task(task_type=TaskType.DEVELOPMENT))
            is GroundingRequirement.REQUIRED
        )

    @pytest.mark.parametrize("task_type", [TaskType.DESIGN, TaskType.RESEARCH])
    def test_non_code_type_without_artifacts_is_not_applicable(
        self, task_type: TaskType
    ) -> None:
        # A non-code task type declaring no code artifact stays NOT_APPLICABLE,
        # so the oracle never blocks a doc / research task.
        assert (
            classify_grounding_requirement(_task(task_type=task_type))
            is GroundingRequirement.NOT_APPLICABLE
        )


class TestBuildTestOracle:
    async def test_none_records_is_checker_unavailable(self) -> None:
        result = await BuildTestOracle().evaluate(
            _task(ArtifactType.CODE), records=None
        )
        assert result.verdict is OracleVerdict.CHECKER_UNAVAILABLE
        assert not result.blocks_completion

    async def test_required_no_records_is_unverified(self) -> None:
        result = await BuildTestOracle().evaluate(
            _task(ArtifactType.CODE), records=_FakeRecords()
        )
        assert result.verdict is OracleVerdict.UNVERIFIED
        assert result.blocks_completion

    async def test_required_latest_passed_is_verified(self) -> None:
        task = _task(ArtifactType.CODE)
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))
        result = await BuildTestOracle().evaluate(task, records=records)
        assert result.verdict is OracleVerdict.VERIFIED
        assert not result.blocks_completion

    async def test_latest_failed_blocks(self) -> None:
        task = _task(ArtifactType.CODE)
        records = _FakeRecords((_record(passed=False, task_id=str(task.id)),))
        result = await BuildTestOracle().evaluate(task, records=records)
        assert result.verdict is OracleVerdict.BUILD_TEST_FAILED
        assert result.blocks_completion

    async def test_latest_run_wins_after_rework(self) -> None:
        # A passing latest run supersedes an earlier failure. Built in the
        # order they ran, so the store has to sort them: written the other way
        # round the assertion would hold for any store that returned its input
        # unchanged, which is what it used to do.
        task = _task(ArtifactType.CODE)
        records = _FakeRecords(
            (
                _record(passed=False, task_id=str(task.id)),
                _record(passed=True, task_id=str(task.id)),
            )
        )
        result = await BuildTestOracle().evaluate(task, records=records)
        assert result.verdict is OracleVerdict.VERIFIED
        assert result.tests_seen == 2
        assert result.tests_failed == 1

    async def test_docs_task_passes_through(self) -> None:
        result = await BuildTestOracle().evaluate(
            _task(ArtifactType.DOCUMENTATION, task_type=TaskType.DESIGN),
            records=_FakeRecords(),
        )
        assert result.verdict is OracleVerdict.NOT_APPLICABLE
        assert not result.blocks_completion

    async def test_required_query_fault_fails_closed(self) -> None:
        result = await BuildTestOracle().evaluate(
            _task(ArtifactType.CODE), records=_FakeRecords(raises=True)
        )
        assert result.verdict is OracleVerdict.UNVERIFIED
        assert result.blocks_completion

    async def test_docs_query_fault_passes_through(self) -> None:
        result = await BuildTestOracle().evaluate(
            _task(ArtifactType.DOCUMENTATION, task_type=TaskType.DESIGN),
            records=_FakeRecords(raises=True),
        )
        assert result.verdict is OracleVerdict.NOT_APPLICABLE
        assert not result.blocks_completion


class TestReadingIsNotDeciding:
    """A listing computes a badge; a gate decides a fate. Only one is an event.

    The dashboard polls the approvals list, which resolves an oracle-block
    flag per finished task. That drove the logging half on a thirty-second
    cadence, so three tasks written off hours earlier kept producing an INFO
    line each, for ever, in the same stream as the gate records that meant
    something.
    """

    async def test_the_verdicts_agree(self) -> None:
        task = _task(ArtifactType.CODE)
        records = _FakeRecords((_record(passed=False, task_id=str(task.id)),))
        oracle = BuildTestOracle()

        decided = await oracle.evaluate(task, records=records)
        read = await oracle.verdict_for(task, records=records)

        assert read == decided

    async def test_only_the_deciding_call_records_one(self) -> None:
        task = _task(ArtifactType.CODE)
        records = _FakeRecords((_record(passed=False, task_id=str(task.id)),))
        oracle = BuildTestOracle()

        with structlog.testing.capture_logs() as captured:
            await oracle.verdict_for(task, records=records)
            read_events = [e.get("event") for e in captured]
        with structlog.testing.capture_logs() as captured:
            await oracle.evaluate(task, records=records)
            decided_events = [e.get("event") for e in captured]

        assert BUILD_TEST_GATE_EVALUATED not in read_events
        # Exactly one, not merely present: the name says the deciding call
        # records ONE, and a membership check passes for any number above zero.
        assert decided_events.count(BUILD_TEST_GATE_EVALUATED) == 1


_MANIFEST = """\
language: python
test_command: pytest
test_report_path: junit.xml
pending:
  - criterion: a score is recorded
    test_id: tests/test_score.py::test_a_score_is_recorded
"""

_PENDING_CASE = (
    '<testcase classname="tests.test_score" file="tests/test_score.py" '
    'name="test_a_score_is_recorded">'
    '<failure message="assert 0 == 1"/></testcase>'
)

#: The criterion the seeded manifest declares pending, in the objective's own
#: wording. Forgiveness is bound to the criteria the plan was approved with,
#: so a plan carrying this is what makes the declaration count.
_APPROVED_CRITERION = "A score is recorded."


def _oracle_for(
    workspace_root: Path,
    *,
    criteria: tuple[str, ...] = (_APPROVED_CRITERION,),
) -> BuildTestOracle:
    """Build the oracle wired the way boot wires it.

    The plan is what carries the criteria the operator approved, and
    forgiveness is bound to them, so an oracle with no plan to read forgives
    nothing and every declared-pending case below would pass for that reason
    rather than the one it is testing.

    Returns:
        An oracle reading *workspace_root* against an approved plan.
    """
    plans = mock_of[PlanRepository](
        get=AsyncMock(
            spec=PlanRepository.get,
            return_value=SimpleNamespace(objective_criteria=criteria),
        ),
    )
    return BuildTestOracle(workspace_root=workspace_root, plans=plans)


def _project_with_manifest(tmp_path: Path, *, report: str | None) -> Path:
    """Seed a project workspace carrying a manifest and optionally a report.

    Returns:
        The base root the oracle is wired with.
    """
    workspace = tmp_path / "projects" / _PROJECT
    workspace.mkdir(parents=True)
    (workspace / DEFAULT_MANIFEST_FILENAME).write_text(_MANIFEST, encoding="utf-8")
    if report is not None:
        (workspace / "junit.xml").write_text(report, encoding="utf-8")
    return tmp_path


def _unit_task(*criteria: str) -> Task:
    """A task implementing one plan item, declaring *criteria*.

    Returns:
        The unit task, keyed to the seeded project.
    """
    return Task(
        title="t",
        description="d",
        type=TaskType.DEVELOPMENT,
        project=_PROJECT,
        plan_id=as_uuid("plan-1"),
        plan_item_id=as_pk("item-a"),
        created_by="c",
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=criterion) for criterion in criteria
        ),
    )


class TestWhatAProjectDeclaredPending:
    """A skeleton's suite fails by design, so the exit status is not the verdict.

    Read here rather than written onto the record: ``CodeExecutionRecord``
    answers "did this exit zero" and a validator holds it to that, so the row
    keeps saying what happened and the oracle says what it means.
    """

    async def test_a_failure_the_project_declared_does_not_block(
        self, tmp_path: Path
    ) -> None:
        task = _unit_task()
        records = _FakeRecords((_record(passed=False, task_id=str(task.id)),))
        oracle = _oracle_for(
            _project_with_manifest(
                tmp_path, report=f"<testsuite>{_PENDING_CASE}</testsuite>"
            )
        )

        result = await oracle.evaluate(task, records=records)

        assert result.verdict is OracleVerdict.VERIFIED
        assert not result.blocks_completion

    async def test_a_failure_it_did_not_declare_still_blocks(
        self, tmp_path: Path
    ) -> None:
        """Forgiveness is the declaration's, so an undeclared break keeps its own."""
        task = _unit_task()
        records = _FakeRecords((_record(passed=False, task_id=str(task.id)),))
        oracle = _oracle_for(
            _project_with_manifest(
                tmp_path,
                report=(
                    f"<testsuite>{_PENDING_CASE}"
                    '<testcase classname="tests/test_other.py" name="test_other">'
                    '<failure message="assert 2 == 3"/></testcase></testsuite>'
                ),
            )
        )

        result = await oracle.evaluate(task, records=records)

        assert result.verdict is OracleVerdict.BUILD_TEST_FAILED
        assert result.blocks_completion

    async def test_a_unit_that_left_its_own_marker_behind_blocks(
        self, tmp_path: Path
    ) -> None:
        """Clearing the entry in the same commit is the signal a unit is done.

        The suite exits zero, so nothing else in the chain can see that the
        next unit is about to inherit a criterion the manifest calls
        unimplemented.
        """
        task = _unit_task("A score is recorded.")
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))
        oracle = _oracle_for(_project_with_manifest(tmp_path, report=None))

        result = await oracle.evaluate(task, records=records)

        assert result.verdict is OracleVerdict.BUILD_TEST_FAILED
        assert result.blocks_completion
        assert "still listed pending" in result.reason

    async def test_another_units_marker_does_not_block_this_one(
        self, tmp_path: Path
    ) -> None:
        """A project mid-build always has other units' entries outstanding."""
        task = _unit_task("something else entirely")
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))
        oracle = _oracle_for(_project_with_manifest(tmp_path, report=None))

        result = await oracle.evaluate(task, records=records)

        assert result.verdict is OracleVerdict.VERIFIED

    async def test_a_stage_job_is_never_held_to_the_markers_it_writes(
        self, tmp_path: Path
    ) -> None:
        """The skeleton's job IS to leave them, so holding it to them refuses it.

        Told apart by ``plan_item_id``: a task carrying a plan id and no item
        id implements no plan item, which is what every stage job looks like.
        """
        task = Task(
            title="Skeleton: ship it",
            description="write the contract",
            type=TaskType.DEVELOPMENT,
            project=_PROJECT,
            plan_id=as_uuid("plan-1"),
            created_by="initiative-skeleton",
            acceptance_criteria=(
                AcceptanceCriterion(description="A score is recorded."),
            ),
        )
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))
        oracle = _oracle_for(_project_with_manifest(tmp_path, report=None))

        result = await oracle.evaluate(task, records=records)

        assert result.verdict is OracleVerdict.VERIFIED

    async def test_a_stage_job_is_never_held_to_the_gates_it_declares(
        self, tmp_path: Path
    ) -> None:
        """Same reasoning as the markers, and the same defect without it.

        The contract job WRITES the gate commands, and its own brief assigns
        running them to "every unit after you". Requiring a passing lint run
        against the skeleton's own task id refuses every contract for doing its
        job, so the stage could never pass and no initiative could get past it.
        """
        task = Task(
            title="Skeleton: ship it",
            description="write the contract",
            type=TaskType.DEVELOPMENT,
            project=_PROJECT,
            plan_id=as_uuid("plan-1"),
            created_by="initiative-skeleton",
        )
        workspace = tmp_path / "projects" / _PROJECT
        workspace.mkdir(parents=True)
        (workspace / DEFAULT_MANIFEST_FILENAME).write_text(
            "language: python\ntest_command: pytest\nlint_command: ruff check .\n",
            encoding="utf-8",
        )
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))

        result = await _oracle_for(tmp_path).evaluate(task, records=records)

        assert result.verdict is OracleVerdict.VERIFIED

    async def test_a_manifest_that_will_not_parse_blocks_rather_than_waiving(
        self, tmp_path: Path
    ) -> None:
        """Reading a broken manifest as "nothing declared" is the worse answer.

        It silently drops the pending set, the clear-your-own-marker rule and
        every declared gate at once, and hands back a verdict whose reason is
        indistinguishable from a compliant project's.
        """
        task = _unit_task()
        workspace = tmp_path / "projects" / _PROJECT
        workspace.mkdir(parents=True)
        (workspace / DEFAULT_MANIFEST_FILENAME).write_text(
            "language: [unclosed", encoding="utf-8"
        )
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))

        result = await _oracle_for(tmp_path).evaluate(task, records=records)

        assert result.verdict is OracleVerdict.UNVERIFIED
        assert result.blocks_completion
        assert "will not parse" in result.reason

    async def test_a_declared_gate_with_no_passing_run_blocks(
        self, tmp_path: Path
    ) -> None:
        """A definition of done nobody enforces is not a definition of done.

        Before anything read these fields a project could declare how it lints,
        never lint, and show a green badge over work no linter ever saw.
        """
        task = _unit_task()
        workspace = tmp_path / "projects" / _PROJECT
        workspace.mkdir(parents=True)
        (workspace / DEFAULT_MANIFEST_FILENAME).write_text(
            "language: python\ntest_command: pytest\nlint_command: ruff check .\n",
            encoding="utf-8",
        )
        records = _FakeRecords((_record(passed=True, task_id=str(task.id)),))

        result = await BuildTestOracle(workspace_root=tmp_path).evaluate(
            task, records=records
        )

        assert result.verdict is OracleVerdict.BUILD_TEST_FAILED
        assert "lint" in result.reason

    async def test_a_declared_gate_that_ran_and_passed_does_not_block(
        self, tmp_path: Path
    ) -> None:
        task = _unit_task()
        workspace = tmp_path / "projects" / _PROJECT
        workspace.mkdir(parents=True)
        (workspace / DEFAULT_MANIFEST_FILENAME).write_text(
            "language: python\ntest_command: pytest\nlint_command: ruff check .\n",
            encoding="utf-8",
        )
        records = _FakeRecords(
            (
                _record(passed=True, task_id=str(task.id)),
                _record(
                    passed=True,
                    task_id=str(task.id),
                    purpose=CodeExecutionPurpose.LINT,
                    command="ruff check .",
                ),
            )
        )

        result = await BuildTestOracle(workspace_root=tmp_path).evaluate(
            task, records=records
        )

        assert result.verdict is OracleVerdict.VERIFIED

    async def test_a_failing_suite_is_reported_before_a_missing_gate(
        self, tmp_path: Path
    ) -> None:
        """Naming the linter alongside a red suite buries what actually broke."""
        task = _unit_task()
        workspace = tmp_path / "projects" / _PROJECT
        workspace.mkdir(parents=True)
        (workspace / DEFAULT_MANIFEST_FILENAME).write_text(
            "language: python\ntest_command: pytest\nlint_command: ruff check .\n",
            encoding="utf-8",
        )
        records = _FakeRecords((_record(passed=False, task_id=str(task.id)),))

        result = await BuildTestOracle(workspace_root=tmp_path).evaluate(
            task, records=records
        )

        assert result.verdict is OracleVerdict.BUILD_TEST_FAILED
        assert "Latest test run failed" in result.reason

    async def test_an_unwired_workspace_keeps_the_pre_pending_behaviour(self) -> None:
        """A boot that resolved no workspace forgives nothing and blocks nothing."""
        task = _unit_task("A score is recorded.")
        oracle = BuildTestOracle()

        failing = await oracle.evaluate(
            task, records=_FakeRecords((_record(passed=False, task_id=str(task.id)),))
        )
        passing = await oracle.evaluate(
            task, records=_FakeRecords((_record(passed=True, task_id=str(task.id)),))
        )

        assert failing.verdict is OracleVerdict.BUILD_TEST_FAILED
        assert passing.verdict is OracleVerdict.VERIFIED
