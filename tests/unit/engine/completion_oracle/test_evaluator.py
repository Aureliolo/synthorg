"""Unit tests for the Layer 1 build/test oracle (classifier + evaluator)."""

from datetime import datetime

import pytest
import structlog.testing

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.completion_oracle.build_test_models import (
    GroundingRequirement,
    OracleVerdict,
)
from synthorg.engine.completion_oracle.classifier import classify_grounding_requirement
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.observability.events.completion_oracle import BUILD_TEST_GATE_EVALUATED
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionPurpose,
    CodeExecutionRecord,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_CLOCK = FakeClock()


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


def _record(*, passed: bool, task_id: str) -> CodeExecutionRecord:
    return CodeExecutionRecord(
        task_id=task_id,
        execution_id="exec-1",
        project_id="p",
        purpose=CodeExecutionPurpose.TESTS,
        command="pytest",
        returncode=0 if passed else 1,
        passed=passed,
        timed_out=False,
        executed_at=_CLOCK.now(),
    )


class _FakeRecords:
    """Minimal in-memory ``CodeExecutionRecordRepository`` for the oracle."""

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
        return self._records

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
        # Newest-first: a passing latest run supersedes an earlier failure.
        task = _task(ArtifactType.CODE)
        records = _FakeRecords(
            (
                _record(passed=True, task_id=str(task.id)),
                _record(passed=False, task_id=str(task.id)),
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
