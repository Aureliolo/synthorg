# module-kind: controller
"""Manual task-decomposition controller.

Lets an operator author a decomposition plan by hand and run it through
the real :class:`~synthorg.engine.decomposition.service.DecompositionService`
(backed by :class:`ManualDecompositionStrategy`): the plan is validated
(unique ids, dependency references, DAG acyclicity, depth / subtask
caps), per-subtask stakes are assessed, the task structure is classified,
and child :class:`~synthorg.core.task.Task` objects are produced. The
endpoint returns the validated :class:`DecompositionResult` so the
dashboard can render the breakdown before it is acted on.

Subtasks are authored with caller-chosen *labels*; the controller maps
each label to a generated UUID (and rewrites dependency references) so
callers never have to mint canonical UUID strings by hand.
"""

from typing import Final
from uuid import uuid4

from litestar import Controller, post
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticError

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_write_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.task_enums import Complexity, CoordinationTopology, Stakes
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.manual import ManualDecompositionStrategy
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND

logger = get_logger(__name__)

_MAX_LABEL_LENGTH: Final[int] = 128
_MAX_TITLE_LENGTH: Final[int] = 200
_MAX_DESCRIPTION_LENGTH: Final[int] = 5000
_DEFAULT_MAX_SUBTASKS: Final[int] = 10
_MAX_SUBTASKS_CAP: Final[int] = 100
_DEFAULT_MAX_DEPTH: Final[int] = 3
_MAX_DEPTH_CAP: Final[int] = 10


class ManualSubtaskSpec(BaseModel):
    """A single hand-authored subtask within a manual decomposition.

    Attributes:
        label: Caller-chosen identifier, unique within the request, used
            to wire dependency references between subtasks.
        title: Short subtask title.
        description: Detailed subtask description.
        dependencies: Labels of other subtasks this one depends on.
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for stakes-aware model routing.
        required_skills: Skill IDs needed for routing.
        required_role: Optional role name for routing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    label: NotBlankStr = Field(
        max_length=_MAX_LABEL_LENGTH,
        description="Caller-chosen subtask label (unique within the request)",
    )
    title: NotBlankStr = Field(
        max_length=_MAX_TITLE_LENGTH,
        description="Short subtask title",
    )
    description: NotBlankStr = Field(
        max_length=_MAX_DESCRIPTION_LENGTH,
        description="Detailed subtask description",
    )
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Labels of subtasks this one depends on",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate for routing",
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL,
        description="Stakes level for stakes-aware model routing",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skill IDs needed for routing",
    )
    required_role: NotBlankStr | None = Field(
        default=None,
        description="Optional role name for routing",
    )


class ManualDecomposeRequest(BaseModel):
    """Request body for a manual decomposition run.

    Attributes:
        subtasks: Hand-authored subtask specs (at least one).
        max_subtasks: Maximum number of subtasks the plan may contain.
        max_depth: Maximum nesting depth for the decomposition context.
        coordination_topology: Selected coordination topology hint.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    subtasks: tuple[ManualSubtaskSpec, ...] = Field(
        min_length=1,
        description="Hand-authored subtask specifications",
    )
    max_subtasks: int = Field(
        default=_DEFAULT_MAX_SUBTASKS,
        ge=1,
        le=_MAX_SUBTASKS_CAP,
        description="Maximum number of subtasks allowed",
    )
    max_depth: int = Field(
        default=_DEFAULT_MAX_DEPTH,
        ge=1,
        le=_MAX_DEPTH_CAP,
        description="Maximum nesting depth",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Selected coordination topology hint",
    )


def _build_plan(
    *,
    parent_task_id: str,
    request: ManualDecomposeRequest,
) -> DecompositionPlan:
    """Map a labelled request into a UUID-keyed decomposition plan.

    Each subtask label is assigned a fresh UUID; dependency references
    are rewritten from labels to the generated UUID strings so the plan
    satisfies the service's canonical-UUID-id contract.

    Returns:
        The validated decomposition plan.

    Raises:
        ValidationError: When labels collide, a dependency references an
            unknown label, or the plan otherwise fails validation.
    """
    labels = [spec.label for spec in request.subtasks]
    if len(labels) != len(set(labels)):
        msg = "Subtask labels must be unique within the request"
        raise ValidationError(msg)

    label_to_id = {label: str(uuid4()) for label in labels}
    subtasks: list[SubtaskDefinition] = []
    for spec in request.subtasks:
        missing = [d for d in spec.dependencies if d not in label_to_id]
        if missing:
            msg = f"Subtask {spec.label!r} references unknown labels: {missing}"
            raise ValidationError(msg)
        subtasks.append(
            SubtaskDefinition(
                id=label_to_id[spec.label],
                title=spec.title,
                description=spec.description,
                dependencies=tuple(label_to_id[d] for d in spec.dependencies),
                estimated_complexity=spec.estimated_complexity,
                stakes=spec.stakes,
                required_skills=spec.required_skills,
                required_role=spec.required_role,
            )
        )
    try:
        return DecompositionPlan(
            parent_task_id=parent_task_id,
            subtasks=tuple(subtasks),
            coordination_topology=request.coordination_topology,
        )
    except PydanticError as exc:
        raise ValidationError(str(exc)) from exc


class DecompositionController(Controller):
    """Manual task decomposition into a validated subtask plan."""

    path = "/tasks"
    tags = ("tasks",)
    guards = [require_write_access]  # noqa: RUF012

    @post(
        "/{task_id:str}/decompose",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("decomposition.manual"),
        ],
    )
    async def decompose_manual(
        self,
        state: State,
        task_id: PathId,
        data: ManualDecomposeRequest,
    ) -> ApiResponse[DecompositionResult]:
        """Run a hand-authored decomposition plan against a task.

        Args:
            state: Application state.
            task_id: Parent task to decompose.
            data: The manual decomposition request.

        Returns:
            The validated decomposition result envelope.

        Raises:
            NotFoundError: When the parent task does not exist.
            ValidationError: When the plan fails validation.
        """
        app_state: AppState = state.app_state
        task_engine = require_service(
            app_state.slice(EngineStateSlice).task_engine, "Task Engine"
        )
        task = await task_engine.get_task(task_id)
        if task is None:
            logger.warning(API_RESOURCE_NOT_FOUND, resource="task", id=task_id)
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)

        plan = _build_plan(parent_task_id=str(task.id), request=data)
        service = DecompositionService(
            ManualDecompositionStrategy(plan),
            TaskStructureClassifier(),
        )
        result = await service.decompose_task(
            task,
            DecompositionContext(
                max_subtasks=data.max_subtasks,
                max_depth=data.max_depth,
            ),
        )
        return ApiResponse(data=result)
