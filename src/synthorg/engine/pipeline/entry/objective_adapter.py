"""Goal / objective work-entry adapter.

Maps a human-stated :class:`ObjectiveSubmission` onto a
:class:`WorkItem` with ``source=OBJECTIVE`` and drives the pipeline
spine. Each objective stands up its **own** initiative project (the
same per-initiative shape the charter approval path uses), minted just
before dispatch from a deterministic id so a retried submission reuses
its project rather than duplicating it. The downstream routing policy
decides solo-vs-team and the
:class:`~synthorg.engine.coordination.service.MultiAgentCoordinator`
handles the goal-to-subtasks decomposition under the ``SPLITTABLE``
verdict.
"""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem, WorkPipelineResult, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.observability import get_logger
from synthorg.observability.events.objectives import (
    OBJECTIVE_PROJECT_PROVISIONED,
    OBJECTIVE_SUBMISSION_RECEIVED,
)
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID = "objective-entry-adapter"

# Stable seed for per-objective initiative project ids so a retried
# submission (same submission id) resolves to the same project rather
# than minting a duplicate.
_PROJECT_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "synthorg:objective-initiative")


class ObjectiveSubmission(BaseModel):
    """The typed envelope a human stated objective enters the spine as.

    Frozen + ``extra="forbid"`` so the controller-boundary
    ``parse_typed`` step rejects unknown fields. Required fields are
    minimal; optional fields fall through to the
    :class:`~synthorg.engine.pipeline.models.WorkItem` defaults so the
    routing policy decides on the work's structure rather than on
    operator-supplied hints.

    Attributes:
        submission_id: Stable correlation id. Defaults to a fresh
            uuid4 so the controller can mint one server-side; clients
            may override for end-to-end tracing.
        title: Short human-readable objective title.
        description: Detailed statement of the objective. Treated as
            untrusted human input; the decomposer wraps task fields
            via ``wrap_untrusted(TAG_TASK_DATA, ...)`` before any LLM
            interpolation (see ``docs/design/coordination.md``).
        requested_by: Identifier of the human or service requesting
            the work (e.g. an operator user id).
        priority: Optional priority override; defaults to
            :class:`WorkItem` default (``MEDIUM``).
        estimated_complexity: Optional complexity override; defaults
            to :class:`WorkItem` default (``MEDIUM``). The shipped
            :class:`LeafThresholdRoutingPolicy` decides routing on
            structure, not complexity, so this is informational.
        task_type: Optional task-type classification; defaults to
            :class:`WorkItem` default (``DEVELOPMENT``).
        acceptance_criteria: Optional acceptance criteria strings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    submission_id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Stable correlation id (auto-generated if absent).",
    )
    title: NotBlankStr = Field(description="Short human-readable objective title.")
    description: NotBlankStr = Field(
        description="Detailed statement of the objective.",
    )
    requested_by: NotBlankStr = Field(
        description="Identifier of the human / service requesting the work.",
    )
    priority: Priority | None = Field(
        default=None,
        description="Optional priority override.",
    )
    estimated_complexity: Complexity | None = Field(
        default=None,
        description="Optional complexity override.",
    )
    task_type: TaskType | None = Field(
        default=None,
        description="Optional task-type classification.",
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional acceptance criteria strings.",
    )


class ObjectiveEntryAdapter:
    """Feeds human-stated objectives into the pipeline spine.

    Unlike the intake adapter this owns a :class:`ProjectRepository`:
    every objective is its own initiative, so the adapter mints a
    dedicated project per submission rather than filing into a shared
    bucket. Minting is idempotent (the project id is derived from the
    submission id), so a redelivered submission reuses its project.
    """

    __slots__ = ("_project_repo", "_work_pipeline")

    def __init__(
        self,
        *,
        work_pipeline: WorkPipeline,
        project_repo: ProjectRepository,
    ) -> None:
        """Initialise the adapter.

        Args:
            work_pipeline: The composed pipeline spine to drive.
            project_repo: Repository used to mint the per-objective
                initiative project before dispatch.
        """
        self._work_pipeline = work_pipeline
        self._project_repo = project_repo

    @property
    def source(self) -> WorkSource:
        """Provenance stamp for items this adapter produces."""
        return WorkSource.OBJECTIVE

    async def submit(self, request: ObjectiveSubmission) -> WorkPipelineResult:
        """Mint the objective's initiative project and drive the spine.

        Optional submission fields (``priority``,
        ``estimated_complexity``, ``task_type``) fall through to the
        :class:`WorkItem` defaults when unset rather than being
        forced by the adapter.

        Args:
            request: The objective submission to enter into the
                pipeline.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: Propagated unchanged from the spine.
        """
        project_id = await self._provision_project(request)
        work_item = self._build_work_item(request, project_id)
        logger.info(
            OBJECTIVE_SUBMISSION_RECEIVED,
            submission_id=request.submission_id,
            project=project_id,
        )
        return await self._work_pipeline.run(work_item)

    async def _provision_project(self, request: ObjectiveSubmission) -> NotBlankStr:
        """Create the objective's own initiative project (idempotent).

        The project id is derived from the submission id so a retried
        submission reuses the same project; a concurrent winner surfaces
        as ``DuplicateRecordError``, which is benign here (the project
        exists, the post-condition we want).

        Returns:
            The minted project's id as a ``NotBlankStr``.
        """
        project_uuid = uuid5(_PROJECT_NAMESPACE, f"objective-{request.submission_id}")
        project_id = NotBlankStr(str(project_uuid))
        if await self._project_repo.get(project_id) is not None:
            return project_id
        try:
            await self._project_repo.create(
                Project(
                    id=project_uuid,
                    name=request.title,
                    description=request.description,
                    status=ProjectStatus.PLANNING,
                )
            )
        except DuplicateRecordError:
            return project_id
        logger.info(
            OBJECTIVE_PROJECT_PROVISIONED,
            submission_id=request.submission_id,
            project=project_id,
        )
        return project_id

    def _build_work_item(
        self,
        request: ObjectiveSubmission,
        project_id: NotBlankStr,
    ) -> WorkItem:
        """Compose the :class:`WorkItem` envelope for the spine.

        Optional submission fields fall through to the WorkItem
        defaults declared on :class:`WorkItem` itself (Priority.MEDIUM,
        TaskType.DEVELOPMENT, Complexity.MEDIUM) when the submission
        does not specify them.

        Returns:
            A :class:`WorkItem` built from the submission with any
            optional fields filled from the WorkItem defaults.
        """
        base = WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.OBJECTIVE,
            title=request.title,
            raw_intent=request.description,
            project=project_id,
            requested_by=request.requested_by,
            acceptance_criteria=request.acceptance_criteria,
            correlation_id=request.submission_id,
        )
        updates: dict[str, Priority | Complexity | TaskType] = {}
        if request.priority is not None:
            updates["priority"] = request.priority
        if request.estimated_complexity is not None:
            updates["estimated_complexity"] = request.estimated_complexity
        if request.task_type is not None:
            updates["task_type"] = request.task_type
        return base.model_copy(update=updates) if updates else base
