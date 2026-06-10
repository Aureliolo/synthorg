# module-kind: code
"""Real-trajectory training-data sourcing for the finetune pipeline.

The embedding finetune used to scan a static document directory. This module
harvests the org's REAL working history into ``{query, positive_passage}``
pairs from three persisted sources, then curates them by the golden-benchmark
score so the model learns from periods the org was demonstrably performing well:

* **Accepted deliverables** -- artifacts of ``COMPLETED`` tasks (query = the
  task title, passage = the artifact's recorded description).
* **Trajectories** -- ``EPISODIC`` "distillation" memories (the agent's
  recorded run summary), keyed back to the task that produced them.
* **Corrected failures** -- ``PROCEDURAL`` ``failure:*`` lessons (the discovery /
  condition / action the org learned from a failed attempt).

Curation: each pair carries the ``created_at`` of its underlying record. An item
is kept only when the benchmark run that graded its creation period was passing
(see :func:`_passes_curation`). With no benchmark history the curation is a
no-op (every harvested pair is kept).
"""

import asyncio
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.distillation import DISTILLATION_TAG
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.meta.learning_curve import LearningCurvePoint, read_learning_curve
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_TRAINING_SOURCE_DEGRADED,
    MEMORY_TRAINING_SOURCE_HARVESTED,
)
from synthorg.persistence.artifact_protocol import (
    ArtifactFilterSpec,
    ArtifactRepository,
)
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository

logger = get_logger(__name__)

# Prefix the procedural-memory pipeline writes onto a failure lesson's
# ``metadata.source`` (``failure:{task_id}``).
_FAILURE_SOURCE_PREFIX: Final[str] = "failure:"
# Leading line of a distillation entry: ``Task ID: {task_id}``. ``MULTILINE``
# anchors ``^`` to any line start so a leading preamble before the marker
# (whitespace, a blank line) does not defeat the search.
_DISTILLATION_TASK_ID: Final[re.Pattern[str]] = re.compile(
    r"^Task ID:\s*(\S+)", re.MULTILINE
)
# Bounds on how much history a single harvest scans.
_MAX_TASKS_PER_STATUS: Final[int] = 1000
_PER_AGENT_MEMORY_LIMIT: Final[int] = 1000


class TrainingPairSource(StrEnum):
    """Which org record a training pair was harvested from."""

    ARTIFACT = "artifact"
    DISTILLATION = "distillation"
    FAILURE_LESSON = "failure_lesson"


class QueryPassagePair(BaseModel):
    """A single ``{query, positive_passage}`` contrastive training example.

    Attributes:
        query: The retrieval query (the task the passage relates to).
        positive_passage: The passage that query should surface.
        source: Which org record this pair was harvested from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    query: NotBlankStr
    positive_passage: NotBlankStr
    source: TrainingPairSource


@runtime_checkable
class TrainingDataSource(Protocol):
    """Produces contrastive training pairs for the embedding finetune."""

    @property
    def name(self) -> str:
        """Stable identifier for logging."""
        ...

    async def collect(
        self,
        cancellation: CancellationToken | None = None,
    ) -> tuple[QueryPassagePair, ...]:
        """Harvest and curate training pairs.

        Args:
            cancellation: Cooperative-cancellation token checked through the
                harvest so a long sweep stays responsive to a cancel request.
        """
        ...


def _passes_curation(
    created_at: datetime | None,
    points: tuple[LearningCurvePoint, ...],
) -> bool:
    """Decide whether a record from *created_at* belongs in the training set.

    The benchmark run that first observed the record is the earliest curve point
    generated at or after ``created_at``; the record is kept only when that run
    passed. Records newer than the most recent run inherit the latest verdict.
    With no benchmark history (or no timestamp) the record is always kept.

    Returns:
        ``True`` when the record should be included.
    """
    if not points or created_at is None:
        return True
    for point in points:
        if point.generated_at >= created_at:
            return point.is_passing
    return points[-1].is_passing


def _task_id_from_distillation(content: str) -> str | None:
    """Extract the task id from a distillation entry's leading line.

    Returns:
        The task id, or ``None`` when the content does not carry one.
    """
    match = _DISTILLATION_TASK_ID.search(content)
    return match.group(1) if match else None


class TrajectoryTrainingDataSource:
    """Harvests embedding training pairs from the org's real working history.

    Args:
        memory_backend: Backend holding EPISODIC distillation and PROCEDURAL
            failure lessons (queried per agent).
        task_repo: Task repository -- the completed/failed task spine supplies
            the agent set, the query titles, and the accepted-deliverable tasks.
        artifact_repo: Artifact repository for accepted-deliverable passages.
        scorecard_history_dir: Golden-benchmark history directory used to curate
            by score. ``None`` disables curation (every pair is kept).
        max_tasks_per_status: Upper bound on tasks scanned per status.
        per_agent_memory_limit: Upper bound on memories pulled per agent.
    """

    def __init__(  # noqa: PLR0913 -- deps plus scan bounds threaded for testability
        self,
        *,
        memory_backend: MemoryBackend,
        task_repo: TaskRepository,
        artifact_repo: ArtifactRepository,
        scorecard_history_dir: Path | None = None,
        max_tasks_per_status: int = _MAX_TASKS_PER_STATUS,
        per_agent_memory_limit: int = _PER_AGENT_MEMORY_LIMIT,
    ) -> None:
        self._memory_backend = memory_backend
        self._task_repo = task_repo
        self._artifact_repo = artifact_repo
        self._scorecard_history_dir = scorecard_history_dir
        self._max_tasks = max_tasks_per_status
        self._per_agent_limit = per_agent_memory_limit

    @property
    def name(self) -> str:
        """Stable identifier for logging.

        Returns:
            The source name.
        """
        return "trajectory"

    async def collect(
        self,
        cancellation: CancellationToken | None = None,
    ) -> tuple[QueryPassagePair, ...]:
        """Harvest the three sources and curate by benchmark score.

        Args:
            cancellation: Cooperative-cancellation token checked before the
                opening queries and inside every per-record loop so a long
                harvest (it sweeps the org's whole working history) stays
                responsive to a cancel request.

        Returns:
            Curated, de-duplicated training pairs.
        """
        if cancellation is not None:
            cancellation.check()
        completed = await self._task_repo.query(
            TaskFilterSpec(status=TaskStatus.COMPLETED),
            limit=self._max_tasks,
        )
        failed = await self._task_repo.query(
            TaskFilterSpec(status=TaskStatus.FAILED),
            limit=self._max_tasks,
        )
        title_by_id = {str(task.id): task.title for task in (*completed, *failed)}
        agent_ids = sorted(
            {
                task.assigned_to
                for task in (*completed, *failed)
                if task.assigned_to is not None
            }
        )

        points = await self._load_curation_points()

        harvested: list[tuple[QueryPassagePair, datetime | None]] = []
        harvested.extend(await self._artifact_pairs(completed, cancellation))
        async with asyncio.TaskGroup() as group:
            agent_tasks = [
                group.create_task(
                    self._harvest_agent(agent_id, title_by_id, cancellation)
                )
                for agent_id in agent_ids
            ]
        for task in agent_tasks:
            harvested.extend(task.result())

        seen: set[tuple[str, str]] = set()
        curated: list[QueryPassagePair] = []
        for pair, created_at in harvested:
            if not _passes_curation(created_at, points):
                continue
            key = (pair.query, pair.positive_passage)
            if key in seen:
                continue
            seen.add(key)
            curated.append(pair)

        by_source = dict.fromkeys(TrainingPairSource, 0)
        for pair in curated:
            by_source[pair.source] += 1
        logger.info(
            MEMORY_TRAINING_SOURCE_HARVESTED,
            source=self.name,
            total=len(curated),
            artifact=by_source[TrainingPairSource.ARTIFACT],
            distillation=by_source[TrainingPairSource.DISTILLATION],
            failure_lesson=by_source[TrainingPairSource.FAILURE_LESSON],
            curated_out=len(harvested) - len(curated),
        )
        return tuple(curated)

    async def _harvest_agent(
        self,
        agent_id: NotBlankStr,
        title_by_id: dict[str, str],
        cancellation: CancellationToken | None = None,
    ) -> list[tuple[QueryPassagePair, datetime | None]]:
        """Harvest one agent's distillation + failure-lesson pairs.

        Per-source failures are isolated inside the pair builders, so a
        single agent's bad data never aborts the concurrent harvest.

        Returns:
            ``(pair, created_at)`` tuples for the agent.
        """
        pairs = await self._distillation_pairs(agent_id, title_by_id, cancellation)
        pairs.extend(await self._failure_pairs(agent_id, title_by_id, cancellation))
        return pairs

    async def _load_curation_points(self) -> tuple[LearningCurvePoint, ...]:
        """Load the benchmark curve points used to curate, if configured.

        Returns:
            Curve points ascending by ``generated_at`` (empty when no history).
        """
        if self._scorecard_history_dir is None:
            return ()
        curve = await asyncio.to_thread(
            read_learning_curve,
            self._scorecard_history_dir,
        )
        return curve.points

    async def _artifact_pairs(
        self,
        completed_tasks: tuple[Task, ...],
        cancellation: CancellationToken | None = None,
    ) -> list[tuple[QueryPassagePair, datetime | None]]:
        """Build pairs from the accepted deliverables of completed tasks.

        Returns:
            ``(pair, created_at)`` tuples for each described artifact.
        """
        pairs: list[tuple[QueryPassagePair, datetime | None]] = []
        for task in completed_tasks:
            if cancellation is not None:
                cancellation.check()
            try:
                artifacts = await self._artifact_repo.query(
                    ArtifactFilterSpec(task_id=str(task.id)),
                    limit=self._per_agent_limit,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                self._log_degraded("artifact", str(task.id), exc)
                continue
            for artifact in artifacts:
                passage = artifact.description.strip()
                if not passage:
                    continue
                pairs.append(
                    (
                        QueryPassagePair(
                            query=task.title,
                            positive_passage=NotBlankStr(passage),
                            source=TrainingPairSource.ARTIFACT,
                        ),
                        artifact.created_at,
                    ),
                )
        return pairs

    async def _distillation_pairs(
        self,
        agent_id: NotBlankStr,
        title_by_id: dict[str, str],
        cancellation: CancellationToken | None = None,
    ) -> list[tuple[QueryPassagePair, datetime | None]]:
        """Build pairs from an agent's EPISODIC distillation memories.

        Returns:
            ``(pair, created_at)`` tuples for each resolvable distillation.
        """
        entries = await self._safe_retrieve(
            agent_id,
            MemoryQuery(
                categories=frozenset({MemoryCategory.EPISODIC}),
                tags=(DISTILLATION_TAG,),
                limit=self._per_agent_limit,
            ),
            kind="distillation",
        )
        pairs: list[tuple[QueryPassagePair, datetime | None]] = []
        for entry in entries:
            if cancellation is not None:
                cancellation.check()
            task_id = _task_id_from_distillation(entry.content)
            title = title_by_id.get(task_id) if task_id else None
            # A blank title or passage would raise a ``NotBlankStr``
            # ``ValidationError`` that aborts the whole TaskGroup harvest;
            # skip the record so one bad entry never sinks the run.
            if title is None or not title.strip() or not entry.content.strip():
                continue
            pairs.append(
                (
                    QueryPassagePair(
                        query=NotBlankStr(title),
                        positive_passage=entry.content,
                        source=TrainingPairSource.DISTILLATION,
                    ),
                    entry.created_at,
                ),
            )
        return pairs

    async def _failure_pairs(
        self,
        agent_id: NotBlankStr,
        title_by_id: dict[str, str],
        cancellation: CancellationToken | None = None,
    ) -> list[tuple[QueryPassagePair, datetime | None]]:
        """Build pairs from an agent's PROCEDURAL ``failure:*`` lessons.

        Returns:
            ``(pair, created_at)`` tuples for each resolvable failure lesson.
        """
        entries = await self._safe_retrieve(
            agent_id,
            MemoryQuery(
                categories=frozenset({MemoryCategory.PROCEDURAL}),
                limit=self._per_agent_limit,
            ),
            kind="failure_lesson",
        )
        pairs: list[tuple[QueryPassagePair, datetime | None]] = []
        for entry in entries:
            if cancellation is not None:
                cancellation.check()
            source = entry.metadata.source
            if not source or not source.startswith(_FAILURE_SOURCE_PREFIX):
                continue
            task_id = source[len(_FAILURE_SOURCE_PREFIX) :]
            title = title_by_id.get(task_id)
            # A blank title or passage would raise a ``NotBlankStr``
            # ``ValidationError`` that aborts the whole TaskGroup harvest;
            # skip the record so one bad entry never sinks the run.
            if title is None or not title.strip() or not entry.content.strip():
                continue
            pairs.append(
                (
                    QueryPassagePair(
                        query=NotBlankStr(title),
                        positive_passage=entry.content,
                        source=TrainingPairSource.FAILURE_LESSON,
                    ),
                    entry.created_at,
                ),
            )
        return pairs

    async def _safe_retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
        *,
        kind: str,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories for one agent, degrading gracefully on failure.

        A single agent's backend error must not abort the whole harvest.

        Returns:
            The retrieved entries, or an empty tuple on failure.
        """
        try:
            return await self._memory_backend.retrieve(agent_id, query)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._log_degraded(kind, agent_id, exc)
            return ()

    def _log_degraded(self, kind: str, ref: str, exc: Exception) -> None:
        """Log a per-source harvest failure without aborting the run."""
        logger.warning(
            MEMORY_TRAINING_SOURCE_DEGRADED,
            source=self.name,
            kind=kind,
            ref=ref,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
