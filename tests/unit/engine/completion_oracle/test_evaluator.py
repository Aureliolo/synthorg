"""Unit tests for the Layer 1 build/test oracle (classifier + evaluator)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.completion_oracle.build_test_models import (
    GroundingRequirement,
    OracleVerdict,
)
from synthorg.engine.completion_oracle.classifier import classify_grounding_requirement
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionPurpose,
    CodeExecutionRecord,
)

pytestmark = pytest.mark.unit


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
        executed_at=datetime.now(UTC),
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
        assert classify_grounding_requirement(_task(declared)) is expected

    def test_no_declared_artifact_is_not_applicable(self) -> None:
        # A task that declares no CODE / TESTS artifact anchors on the same
        # ``artifacts_expected`` signal the gate acts on, so the gate verdict
        # and read-layer re-source agree it is NOT_APPLICABLE.
        assert (
            classify_grounding_requirement(_task())
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
            _task(ArtifactType.DOCUMENTATION), records=_FakeRecords()
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
            _task(ArtifactType.DOCUMENTATION), records=_FakeRecords(raises=True)
        )
        assert result.verdict is OracleVerdict.NOT_APPLICABLE
        assert not result.blocks_completion
