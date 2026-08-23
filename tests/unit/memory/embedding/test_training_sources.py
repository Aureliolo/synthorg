# module-kind: tests
"""Tests for the real-trajectory training-data source.

The finetune sources training pairs from the org's REAL working history --
accepted deliverables (completed-task artifacts), trajectories (EPISODIC
distillation memories), and corrected failures (PROCEDURAL ``failure:*``
lessons) -- and curates them by the golden-benchmark score. The capture and
retrieve paths are the real ones (a live ``InMemoryBackend`` plus in-memory task
and artifact repos); only the LLM-driven capture upstream is out of scope here.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, override

import pytest

from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.consolidation.distillation import DISTILLATION_TAG
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.training_sources import (
    QueryPassagePair,
    TrainingPairSource,
    TrajectoryTrainingDataSource,
    _passes_curation,
)
from synthorg.memory.errors import FineTuneCancelledError
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.meta.learning_curve import ScorecardSummary, append_summary
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.task_protocol import TaskFilterSpec
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_AGENT: Final[str] = "agent-001"
_DELIVERABLE: Final[str] = "A resilient checkout flow with retry and recovery."
_TRAJECTORY: Final[str] = (
    f"Task ID: {sid('task-done')}\nOutcome: success\n"
    "Trajectory: recovered from the first failure\nMemory tool invocations: none"
)
_LESSON: Final[str] = (
    "[DISCOVERY] retry payment on timeout\n\n[CONDITION] payment times out\n\n"
    "[ACTION] retry with exponential backoff\n\n[RATIONALE] timeouts are transient\n\n"
    "[EXECUTION]\n  1. detect timeout\n  2. retry"
)


class _FakeTaskRepository:
    """In-memory task repo exposing the full TaskRepository surface."""

    def __init__(self, tasks: tuple[Task, ...]) -> None:
        self._tasks = {str(task.id): task for task in tasks}

    async def save(self, entity: Task) -> None:
        self._tasks[str(entity.id)] = entity

    async def save_many(self, entities: tuple[Task, ...]) -> None:
        for entity in entities:
            self._tasks[str(entity.id)] = entity

    async def get(self, entity_id: str) -> Task | None:
        return self._tasks.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self._tasks.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Task, ...]:
        ordered = sorted(self._tasks.values(), key=lambda t: t.id)
        return tuple(ordered[offset : offset + limit])

    async def query(
        self, filter_spec: TaskFilterSpec, *, limit: int = 100, offset: int = 0
    ) -> tuple[Task, ...]:
        matched = [
            t
            for t in sorted(self._tasks.values(), key=lambda t: t.id)
            if filter_spec.status is None or t.status == filter_spec.status
        ]
        return tuple(matched[offset : offset + limit])

    async def count(self, filter_spec: TaskFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=10_000))


class _FakeArtifactRepository:
    """In-memory artifact repo exposing the full ArtifactRepository surface."""

    def __init__(self, artifacts: tuple[Artifact, ...]) -> None:
        self._artifacts = {artifact.id: artifact for artifact in artifacts}

    async def save(self, entity: Artifact) -> None:
        self._artifacts[entity.id] = entity

    async def save_returning_outcome(self, artifact: Artifact) -> bool:
        inserted = artifact.id not in self._artifacts
        self._artifacts[artifact.id] = artifact
        return inserted

    async def get(self, entity_id: str) -> Artifact | None:
        return self._artifacts.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self._artifacts.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Artifact, ...]:
        ordered = sorted(self._artifacts.values(), key=lambda a: a.id)
        return tuple(ordered[offset : offset + limit])

    async def query(
        self, filter_spec: ArtifactFilterSpec, *, limit: int = 100, offset: int = 0
    ) -> tuple[Artifact, ...]:
        matched = [
            a
            for a in sorted(self._artifacts.values(), key=lambda a: a.id)
            if filter_spec.task_id is None or a.task_id == filter_spec.task_id
        ]
        return tuple(matched[offset : offset + limit])

    async def count(self, filter_spec: ArtifactFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=10_000))


def _task(task_id: str, title: str, status: TaskStatus) -> Task:
    return Task(
        id=as_uuid(task_id),
        title=NotBlankStr(title),
        description=NotBlankStr(f"Description for {title}"),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr("project-001"),
        created_by=NotBlankStr("manager-001"),
        assigned_to=NotBlankStr(_AGENT),
        status=status,
    )


def _artifact(artifact_id: str, task_id: str, description: str) -> Artifact:
    return Artifact(
        id=NotBlankStr(artifact_id),
        type=ArtifactType.CODE,
        path=NotBlankStr(f"src/{artifact_id}.py"),
        task_id=NotBlankStr(task_id),
        created_by=NotBlankStr(_AGENT),
        description=description,
        created_at=datetime.now(UTC),
    )


async def _seeded_backend() -> InMemoryBackend:
    """A backend carrying a distillation trajectory and a failure lesson."""
    backend = InMemoryBackend()
    await backend.connect()
    await backend.store(
        NotBlankStr(_AGENT),
        MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content=NotBlankStr(_TRAJECTORY),
            metadata=MemoryMetadata(
                source=NotBlankStr("distillation"),
                tags=(DISTILLATION_TAG,),
            ),
        ),
    )
    await backend.store(
        NotBlankStr(_AGENT),
        MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            content=NotBlankStr(_LESSON),
            metadata=MemoryMetadata(
                source=NotBlankStr(f"failure:{sid('task-failed')}")
            ),
        ),
    )
    return backend


def _source(
    backend: InMemoryBackend,
    *,
    history_dir: Path | None = None,
) -> TrajectoryTrainingDataSource:
    tasks = (
        _task("task-done", "Build the checkout flow", TaskStatus.COMPLETED),
        _task("task-failed", "Handle the payment timeout", TaskStatus.FAILED),
    )
    return TrajectoryTrainingDataSource(
        memory_backend=backend,
        task_repo=_FakeTaskRepository(tasks),
        artifact_repo=_FakeArtifactRepository(
            (_artifact("artifact-001", sid("task-done"), _DELIVERABLE),),
        ),
        scorecard_history_dir=history_dir,
    )


async def test_harvests_pairs_from_all_three_sources() -> None:
    """Collect yields accepted-deliverable, trajectory, and failure pairs."""
    backend = await _seeded_backend()
    pairs = await _source(backend).collect()

    by_source = {pair.source: pair for pair in pairs}
    assert set(by_source) == {
        TrainingPairSource.ARTIFACT,
        TrainingPairSource.DISTILLATION,
        TrainingPairSource.FAILURE_LESSON,
    }
    # The artifact pair maps the completed task's title to its deliverable.
    assert by_source[TrainingPairSource.ARTIFACT].query == "Build the checkout flow"
    assert by_source[TrainingPairSource.ARTIFACT].positive_passage == _DELIVERABLE
    # The distillation pair recovers the task title from the trajectory content.
    assert by_source[TrainingPairSource.DISTILLATION].query == "Build the checkout flow"
    assert by_source[TrainingPairSource.DISTILLATION].positive_passage == _TRAJECTORY
    # The failure lesson maps the failed task's title to the corrected approach.
    assert (
        by_source[TrainingPairSource.FAILURE_LESSON].query
        == "Handle the payment timeout"
    )
    assert by_source[TrainingPairSource.FAILURE_LESSON].positive_passage == _LESSON


class _CancelsOnNthCheck(CancellationToken):
    """Fires cancellation from inside the harvest, where the loops check it.

    Cancelling up front would only ever reach the first check. Counting them
    lets each loop's own check be the one that raises.
    """

    def __init__(self, nth: int) -> None:
        super().__init__()
        self.nth = nth
        self.seen = 0
        self.stages: list[str] = []

    @override
    def check(self, *, stage: str) -> None:
        """Count this check, cancel once the target is reached, then defer.

        Args:
            stage: What was interrupted, recorded so the test can assert the
                loops name themselves distinctly.
        """
        self.seen += 1
        self.stages.append(stage)
        if self.seen >= self.nth:
            self.cancel()
        super().check(stage=stage)


#: Which check number each harvest loop's own ``cancellation.check`` is, in
#: collect() order. One per loop, because each owns its check: a single count
#: would leave the others asserted by nothing.
_CHECK_AT_COLLECT_ENTRY: Final[int] = 1
_CHECK_IN_ARTIFACT_HARVEST: Final[int] = 2
_CHECK_IN_DISTILLATION_HARVEST: Final[int] = 3
_CHECK_IN_FAILURE_HARVEST: Final[int] = 4


@pytest.mark.parametrize(
    "checks_before_cancel",
    [
        _CHECK_AT_COLLECT_ENTRY,
        _CHECK_IN_ARTIFACT_HARVEST,
        _CHECK_IN_DISTILLATION_HARVEST,
        _CHECK_IN_FAILURE_HARVEST,
    ],
)
async def test_collect_is_cancellable_in_every_harvest_loop(
    checks_before_cancel: int,
) -> None:
    """Harvesting scans the org's whole history, so it must stop when asked.

    Each loop carries its own check and names its own stage, because giving up
    on a corpus scan and giving up on hours of GPU time are different losses
    and the operator reads the difference in the log.
    """
    backend = await _seeded_backend()
    token = _CancelsOnNthCheck(checks_before_cancel)

    with pytest.raises(FineTuneCancelledError) as excinfo:
        await _source(backend).collect(cancellation=token)

    # The message names the stage that was interrupted, not just "cancelled".
    assert "cancelled during" in str(excinfo.value)
    assert token.stages[-1] in str(excinfo.value)


async def test_collect_without_a_token_never_cancels() -> None:
    """The token is optional; absent, every harvest runs to completion."""
    backend = await _seeded_backend()

    pairs = await _source(backend).collect(cancellation=None)

    assert len(pairs) > 0


async def test_curation_drops_items_graded_by_a_failing_run(tmp_path: Path) -> None:
    """A failing benchmark window curates every harvested pair out."""
    backend = await _seeded_backend()
    future = datetime.now(UTC) + timedelta(hours=1)
    append_summary(
        tmp_path,
        ScorecardSummary(
            run_label=NotBlankStr("failing-run"),
            generated_at=future,
            total=10,
            max_total=100,
            is_passing=False,
        ),
    )

    pairs = await _source(backend, history_dir=tmp_path).collect()

    assert pairs == ()


async def test_curation_keeps_items_graded_by_a_passing_run(tmp_path: Path) -> None:
    """A passing benchmark window keeps the harvested pairs."""
    backend = await _seeded_backend()
    future = datetime.now(UTC) + timedelta(hours=1)
    append_summary(
        tmp_path,
        ScorecardSummary(
            run_label=NotBlankStr("passing-run"),
            generated_at=future,
            total=90,
            max_total=100,
            is_passing=True,
        ),
    )

    pairs = await _source(backend, history_dir=tmp_path).collect()

    assert len(pairs) == 3


async def test_no_history_keeps_every_pair() -> None:
    """With no benchmark history, curation is a no-op."""
    backend = await _seeded_backend()
    pairs = await _source(backend, history_dir=None).collect()
    assert len(pairs) == 3


async def test_distillation_for_unknown_task_is_skipped() -> None:
    """A trajectory pointing at a task outside the scan is not emitted."""
    backend = InMemoryBackend()
    await backend.connect()
    await backend.store(
        NotBlankStr(_AGENT),
        MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content=NotBlankStr("Task ID: ghost-task\nOutcome: success"),
            metadata=MemoryMetadata(
                source=NotBlankStr("distillation"),
                tags=(DISTILLATION_TAG,),
            ),
        ),
    )
    source = TrajectoryTrainingDataSource(
        memory_backend=backend,
        task_repo=_FakeTaskRepository(
            (_task("task-done", "Known task", TaskStatus.COMPLETED),),
        ),
        artifact_repo=_FakeArtifactRepository(()),
    )
    pairs = await source.collect()
    assert all(p.source != TrainingPairSource.DISTILLATION for p in pairs)


async def test_blank_artifact_description_is_skipped() -> None:
    """An artifact with no recorded description yields no pair."""
    backend = InMemoryBackend()
    await backend.connect()
    source = TrajectoryTrainingDataSource(
        memory_backend=backend,
        task_repo=_FakeTaskRepository(
            (_task("task-done", "Known task", TaskStatus.COMPLETED),),
        ),
        artifact_repo=_FakeArtifactRepository(
            (_artifact("artifact-001", sid("task-done"), "   "),),
        ),
    )
    pairs = await source.collect()
    assert pairs == ()


def _point(label: str, *, hour: int, passing: bool) -> ScorecardSummary:
    return ScorecardSummary(
        run_label=NotBlankStr(label),
        generated_at=datetime(2026, 4, 11, hour, tzinfo=UTC),
        total=90 if passing else 10,
        max_total=100,
        is_passing=passing,
    )


def test_passes_curation_windows() -> None:
    """The curation predicate grades each item by the run that first saw it."""
    from synthorg.meta.learning_curve import assemble_curve

    curve = assemble_curve(
        (
            _point("r1-fail", hour=2, passing=False),
            _point("r2-pass", hour=4, passing=True),
        ),
    )
    points = curve.points
    before_first = datetime(2026, 4, 11, 1, tzinfo=UTC)
    between = datetime(2026, 4, 11, 3, tzinfo=UTC)
    after_last = datetime(2026, 4, 11, 5, tzinfo=UTC)

    # Graded by the first (failing) run.
    assert _passes_curation(before_first, points) is False
    # Graded by the second (passing) run.
    assert _passes_curation(between, points) is True
    # Newer than the last run inherits the latest (passing) verdict.
    assert _passes_curation(after_last, points) is True
    # No history or no timestamp: always kept.
    assert _passes_curation(between, ()) is True
    assert _passes_curation(None, points) is True


def test_query_passage_pair_is_frozen() -> None:
    """Pairs are immutable value objects."""
    pair = QueryPassagePair(
        query=NotBlankStr("q"),
        positive_passage=NotBlankStr("p"),
        source=TrainingPairSource.ARTIFACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        pair.query = NotBlankStr("other")  # type: ignore[misc]
