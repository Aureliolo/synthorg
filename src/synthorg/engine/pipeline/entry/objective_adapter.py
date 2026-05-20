"""Goal / objective work-entry adapter.

Maps a human-stated :class:`ObjectiveSubmission` onto a
:class:`WorkItem` with ``source=OBJECTIVE`` and drives the pipeline
spine. Thin by design: the adapter owns no persistence, no
decomposition logic, no rejection state. The downstream routing
policy decides solo-vs-team and the
:class:`~synthorg.engine.coordination.service.MultiAgentCoordinator`
handles the goal-to-subtasks decomposition under the ``SPLITTABLE``
verdict.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import Complexity, Priority, TaskType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.observability import get_logger
from synthorg.observability.events.objectives import OBJECTIVE_SUBMISSION_RECEIVED

if TYPE_CHECKING:
    from synthorg.engine.pipeline.models import WorkPipelineResult
    from synthorg.engine.pipeline.protocol import WorkPipeline

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID = "objective-entry-adapter"


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
    """Feeds human-stated objectives into the pipeline spine."""

    __slots__ = ("_default_project", "_work_pipeline")

    def __init__(
        self,
        *,
        work_pipeline: WorkPipeline,
        default_project: NotBlankStr,
    ) -> None:
        """Initialise the adapter.

        Args:
            work_pipeline: The composed pipeline spine to drive.
            default_project: Project every objective work item is
                filed into (created at boot if absent by
                :func:`wire_real_objective_entry`).
        """
        self._work_pipeline = work_pipeline
        self._default_project = default_project

    @property
    def source(self) -> WorkSource:
        """Provenance stamp for items this adapter produces."""
        return WorkSource.OBJECTIVE

    async def submit(self, request: ObjectiveSubmission) -> WorkPipelineResult:
        """Map ``request`` onto a work item and drive the spine.

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
        work_item = self._build_work_item(request)
        logger.info(
            OBJECTIVE_SUBMISSION_RECEIVED,
            submission_id=request.submission_id,
            project=self._default_project,
        )
        return await self._work_pipeline.run(work_item)

    def _build_work_item(self, request: ObjectiveSubmission) -> WorkItem:
        """Compose the :class:`WorkItem` envelope for the spine.

        Optional submission fields fall through to the WorkItem
        defaults declared on :class:`WorkItem` itself (Priority.MEDIUM,
        TaskType.DEVELOPMENT, Complexity.MEDIUM) when the submission
        does not specify them.
        """
        base = WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.OBJECTIVE,
            title=request.title,
            raw_intent=request.description,
            project=self._default_project,
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
