"""Brownfield codebase-intake endpoint at ``/brownfield/import``.

A thin HTTP boundary over the
:class:`~synthorg.engine.pipeline.entry.brownfield_adapter.BrownfieldEntryAdapter`.
``POST /brownfield/import`` accepts a source reference + target project,
spawns the import + analysis pipeline run as a background task, and
returns ``202 Accepted`` with the project id so callers correlate by it.
The import (clone/copy + scan + index) and the agent analysis pass run
asynchronously; the operator polls the project's structure map / tasks.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from litestar import Controller, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.models import CodebaseImportSubmission
from synthorg.observability import get_logger
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.brownfield import (
    BROWNFIELD_IMPORT_STARTED,
    BROWNFIELD_PIPELINE_FAILED,
)

if TYPE_CHECKING:
    from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter

logger = get_logger(__name__)

_ACCEPTED_STATUS = "accepted"


class ImportCodebasePayload(BaseModel):
    """HTTP body for ``POST /brownfield/import``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Target project to import into.")
    source_ref: NotBlankStr = Field(
        description="Remote clone URL or local path to import from.",
    )
    title: NotBlankStr = Field(
        default=NotBlankStr("Imported codebase"),
        description="Title for the indexed knowledge source.",
    )
    requested_by: NotBlankStr = Field(
        default=NotBlankStr("operator"),
        description="Identifier of the human / service requesting the import.",
    )
    default_branch: NotBlankStr = Field(
        default=NotBlankStr("main"),
        description="Branch to provision and seed.",
    )


class ImportCodebaseAck(BaseModel):
    """``202 Accepted`` response body."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(
        description="The project the codebase is being imported into.",
    )
    status: NotBlankStr = Field(
        description='Lifecycle marker; always ``"accepted"`` on 202.',
    )


async def import_codebase_impl(
    app_state: AppState,
    data: ImportCodebasePayload,
) -> ApiResponse[ImportCodebaseAck]:
    """Spawn the import + analysis run and return a ``202``-shaped ack.

    Public to the package for testability (direct calls in unit tests),
    not for external callers.
    """
    submission = CodebaseImportSubmission(
        project_id=data.project_id,
        source_ref=data.source_ref,
        title=data.title,
        requested_by=data.requested_by,
        default_branch=data.default_branch,
    )
    adapter = app_state.brownfield_entry_adapter
    logger.info(BROWNFIELD_IMPORT_STARTED, project_id=submission.project_id)
    task = asyncio.create_task(_drive_pipeline(adapter, submission))
    task.add_done_callback(
        log_task_exceptions(
            logger,
            BROWNFIELD_PIPELINE_FAILED,
            project_id=submission.project_id,
        ),
    )
    app_state.brownfield_background_tasks.add(task)
    task.add_done_callback(app_state.brownfield_background_tasks.discard)
    return ApiResponse(
        data=ImportCodebaseAck(
            project_id=submission.project_id,
            status=NotBlankStr(_ACCEPTED_STATUS),
        )
    )


class BrownfieldController(Controller):
    """Brownfield codebase-intake endpoints."""

    path = "/brownfield"
    tags = ("brownfield",)
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/import",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("brownfield.import", key="user"),
        ],
        opt=per_op_concurrency_from_policy("brownfield.import", key="user"),
        status_code=202,
    )
    async def import_codebase(
        self,
        state: State,
        data: ImportCodebasePayload,
    ) -> ApiResponse[ImportCodebaseAck]:
        """Import an existing codebase and run the analysis pass.

        The import seeds the project workspace from ``source_ref``, scans
        it into a navigable structure map, indexes it into the knowledge
        store, then drives an ANALYSIS work item through the pipeline
        spine. Returns ``202 Accepted`` immediately; the run completes
        asynchronously.
        """
        return await import_codebase_impl(state.app_state, data)


async def _drive_pipeline(
    adapter: WorkEntryAdapter[Any], submission: CodebaseImportSubmission
) -> None:
    """Run the adapter's import + pipeline submission, discarding the result.

    The terminal :class:`WorkPipelineResult` is intentionally discarded:
    callers correlate to the project's structure map and spawned task.
    """
    await adapter.submit(submission)
