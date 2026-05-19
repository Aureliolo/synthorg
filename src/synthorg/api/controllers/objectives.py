"""Goal / objective intake endpoints at ``/objectives``.

A thin HTTP boundary over the
:class:`~synthorg.engine.pipeline.entry.objective_adapter.ObjectiveEntryAdapter`.
``POST /objectives`` mints a submission id, spawns the pipeline run
as a background task, and returns ``202 Accepted`` with the
submission id. The submission id is threaded through to the spawned
root task as its idempotency key (see
``engine/pipeline/service.py``), so callers correlate by that id.
"""

import asyncio
from typing import Any
from uuid import uuid4

from litestar import Controller, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveSubmission
from synthorg.observability import get_logger
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.objectives import (
    OBJECTIVE_PIPELINE_FAILED,
    OBJECTIVE_SUBMISSION_RECEIVED,
)

logger = get_logger(__name__)


class SubmitObjectivePayload(BaseModel):
    """HTTP body for ``POST /objectives``.

    Mirrors :class:`ObjectiveSubmission` but with no
    ``submission_id`` field: the server mints one so concurrent
    submissions cannot collide.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(description="Short human-readable objective title.")
    description: NotBlankStr = Field(
        description="Detailed statement of the objective.",
    )
    requested_by: NotBlankStr = Field(
        description="Identifier of the human / service requesting the work.",
    )
    priority: str | None = Field(
        default=None,
        description="Optional priority override (Priority enum value).",
    )
    estimated_complexity: str | None = Field(
        default=None,
        description="Optional complexity override (Complexity enum value).",
    )
    task_type: str | None = Field(
        default=None,
        description="Optional task-type override (TaskType enum value).",
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional acceptance criteria strings.",
    )


class SubmitObjectiveAck(BaseModel):
    """``202 Accepted`` response body."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    submission_id: NotBlankStr = Field(
        description=(
            "Server-minted correlation id. Used as the WorkItem's"
            " correlation_id and the spawned root task's idempotency"
            " key, so callers can correlate this submission to the"
            " spawned task once it exists."
        ),
    )
    status: NotBlankStr = Field(
        description='Lifecycle marker; always ``"accepted"`` on 202.',
    )


_ACCEPTED_STATUS = "accepted"


async def submit_objective_impl(
    app_state: AppState,
    data: SubmitObjectivePayload,
) -> ApiResponse[SubmitObjectiveAck]:
    """Spawn the pipeline run and return ``202``-shaped acknowledgement.

    The controller-method shim delegates to this helper so the same
    behaviour is exercised by direct calls (unit tests) and HTTP
    requests (integration tests / production). Public to the package
    for testability, not for external callers.
    """
    submission = _build_submission(data)
    adapter = app_state.objective_entry_adapter
    logger.info(
        OBJECTIVE_SUBMISSION_RECEIVED,
        submission_id=submission.submission_id,
        requested_by=submission.requested_by,
    )
    task = asyncio.create_task(_drive_pipeline(adapter, submission))
    task.add_done_callback(
        log_task_exceptions(
            logger,
            OBJECTIVE_PIPELINE_FAILED,
            submission_id=submission.submission_id,
        ),
    )
    task.add_done_callback(app_state.objective_background_tasks.discard)
    app_state.objective_background_tasks.add(task)
    return ApiResponse(
        data=SubmitObjectiveAck(
            submission_id=submission.submission_id,
            status=_ACCEPTED_STATUS,
        )
    )


class ObjectiveController(Controller):
    """Goal / objective intake endpoints."""

    path = "/objectives"
    tags = ("objectives",)
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("objectives.submit", key="user"),
        ],
        status_code=202,
    )
    async def submit_objective(
        self,
        state: State,
        data: SubmitObjectivePayload,
    ) -> ApiResponse[SubmitObjectiveAck]:
        """Submit a goal/objective for autonomous decomposition.

        The pipeline spine routes the submitted objective through
        ``intake -> projects -> decompose -> solo/team -> execute``;
        the multi-agent coordinator handles the goal-to-subtasks
        decomposition under the ``SPLITTABLE`` verdict.

        Returns ``202 Accepted`` immediately with the minted
        ``submission_id`` so the HTTP response does not block on the
        full pipeline run.
        """
        return await submit_objective_impl(state.app_state, data)


def _build_submission(data: SubmitObjectivePayload) -> ObjectiveSubmission:
    """Map the HTTP payload to a typed :class:`ObjectiveSubmission`.

    Optional enum fields are passed through as strings; Pydantic
    coerces them against the enum members defined on
    :class:`ObjectiveSubmission`, raising a validation error if a
    caller supplies an unknown value.
    """
    fields: dict[str, Any] = {
        "submission_id": str(uuid4()),
        "title": data.title,
        "description": data.description,
        "requested_by": data.requested_by,
        "acceptance_criteria": data.acceptance_criteria,
    }
    if data.priority is not None:
        fields["priority"] = data.priority
    if data.estimated_complexity is not None:
        fields["estimated_complexity"] = data.estimated_complexity
    if data.task_type is not None:
        fields["task_type"] = data.task_type
    return ObjectiveSubmission(**fields)


async def _drive_pipeline(adapter: Any, submission: ObjectiveSubmission) -> None:
    """Run the adapter's pipeline submission and discard the result.

    Wrapping the awaitable in an ``async def`` lets the controller's
    background-task discipline (set-tracking + done-callback exception
    routing) own the lifecycle. The terminal :class:`WorkPipelineResult`
    is intentionally discarded here: callers correlate to the spawned
    root task via the submission id (set as the WorkItem's
    correlation_id).
    """
    await adapter.submit(submission)
