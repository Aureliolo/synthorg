"""Domain event payloads.

Covers artifact, project, memory fine-tune, client, request, review,
simulation, interrupt/dissent.  The second half of the WebSocket
discriminated-union surface; see ``synthorg.api.ws_payloads.__init__``
for the union + re-exports.
"""

from typing import Literal

from pydantic import BaseModel, Field

from synthorg.api.ws_models import WsEventType
from synthorg.api.ws_payloads._base import PAYLOAD_CONFIG
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

# ── Artifact domain ─────────────────────────────────────────────────


class WsArtifactCreatedPayload(BaseModel):
    """Payload for ``artifact.created``.

    Emitted by ``api/controllers/artifacts.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.ARTIFACT_CREATED] = WsEventType.ARTIFACT_CREATED
    artifact_id: NotBlankStr
    task_id: NotBlankStr
    created_by: NotBlankStr
    type: NotBlankStr


class WsArtifactDeletedPayload(BaseModel):
    """Payload for ``artifact.deleted``.

    Emitted by ``api/controllers/artifacts.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.ARTIFACT_DELETED] = WsEventType.ARTIFACT_DELETED
    artifact_id: NotBlankStr
    task_id: NotBlankStr


class WsArtifactContentUploadedPayload(BaseModel):
    """Payload for ``artifact.content_uploaded``.

    Emitted by ``api/controllers/artifacts.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.ARTIFACT_CONTENT_UPLOADED] = (
        WsEventType.ARTIFACT_CONTENT_UPLOADED
    )
    artifact_id: NotBlankStr
    size_bytes: int = Field(ge=0)
    content_type: NotBlankStr


# ── Project domain ──────────────────────────────────────────────────


class WsProjectCreatedPayload(BaseModel):
    """Payload for ``project.created``.

    Emitted by ``api/controllers/projects.py`` ``create_project``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.PROJECT_CREATED] = WsEventType.PROJECT_CREATED
    project_id: NotBlankStr
    name: NotBlankStr
    status: NotBlankStr
    lead: NotBlankStr | None = None


class WsProjectDeletedPayload(BaseModel):
    """Payload for ``project.deleted``.

    Emitted by ``api/controllers/projects.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.PROJECT_DELETED] = WsEventType.PROJECT_DELETED
    project_id: NotBlankStr
    name: NotBlankStr


class WsProjectStatusChangedPayload(BaseModel):
    """Payload for ``project.status_changed``.

    Reserved for the future project-status endpoint; not yet emitted
    by Python.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.PROJECT_STATUS_CHANGED] = (
        WsEventType.PROJECT_STATUS_CHANGED
    )
    project_id: NotBlankStr
    status: NotBlankStr
    previous_status: NotBlankStr | None = None


# ── Memory fine-tune domain ─────────────────────────────────────────


class _MemoryFineTuneBase(BaseModel):
    """Shared shape for memory fine-tune lifecycle events.

    Emitted by ``memory/embedding/fine_tune_orchestrator.py:_emit_ws``.
    Every variant carries ``run_id``, current ``stage``, and optional
    ``progress`` (0.0-1.0).
    """

    model_config = PAYLOAD_CONFIG

    run_id: NotBlankStr
    stage: NotBlankStr
    progress: float | None = Field(default=None, ge=0.0, le=1.0)


class WsMemoryFineTuneProgressPayload(_MemoryFineTuneBase):
    """Payload for ``memory.fine_tune.progress``."""

    event_type: Literal[WsEventType.MEMORY_FINE_TUNE_PROGRESS] = (
        WsEventType.MEMORY_FINE_TUNE_PROGRESS
    )


class WsMemoryFineTuneStageChangedPayload(_MemoryFineTuneBase):
    """Payload for ``memory.fine_tune.stage_changed``."""

    event_type: Literal[WsEventType.MEMORY_FINE_TUNE_STAGE_CHANGED] = (
        WsEventType.MEMORY_FINE_TUNE_STAGE_CHANGED
    )
    previous_stage: NotBlankStr | None = None


class WsMemoryFineTuneCompletedPayload(_MemoryFineTuneBase):
    """Payload for ``memory.fine_tune.completed``."""

    event_type: Literal[WsEventType.MEMORY_FINE_TUNE_COMPLETED] = (
        WsEventType.MEMORY_FINE_TUNE_COMPLETED
    )


class WsMemoryFineTuneFailedPayload(_MemoryFineTuneBase):
    """Payload for ``memory.fine_tune.failed``.

    Carries optional error string when the orchestrator persisted the
    failure cause; empty when only an in-memory transition was made.
    """

    event_type: Literal[WsEventType.MEMORY_FINE_TUNE_FAILED] = (
        WsEventType.MEMORY_FINE_TUNE_FAILED
    )
    error: str | None = None


# ── Client domain ───────────────────────────────────────────────────


class _ClientEventBase(BaseModel):
    """Shared shape for client lifecycle events.

    Emitted by ``api/controllers/clients.py:_publish_client_event``.
    """

    model_config = PAYLOAD_CONFIG

    client_id: NotBlankStr
    name: NotBlankStr
    strictness_level: float = Field(
        ge=0.0,
        le=1.0,
        description="Review strictness (0.0=lenient, 1.0=strict)",
    )


class WsClientCreatedPayload(_ClientEventBase):
    """Payload for ``client.created``."""

    event_type: Literal[WsEventType.CLIENT_CREATED] = WsEventType.CLIENT_CREATED


class WsClientUpdatedPayload(_ClientEventBase):
    """Payload for ``client.updated``."""

    event_type: Literal[WsEventType.CLIENT_UPDATED] = WsEventType.CLIENT_UPDATED


class WsClientDeactivatedPayload(_ClientEventBase):
    """Payload for ``client.deactivated``."""

    event_type: Literal[WsEventType.CLIENT_DEACTIVATED] = WsEventType.CLIENT_DEACTIVATED


class WsClientDeletedPayload(BaseModel):
    """Payload for ``client.deleted`` -- not yet emitted by Python."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.CLIENT_DELETED] = WsEventType.CLIENT_DELETED
    client_id: NotBlankStr
    name: NotBlankStr | None = None


# ── Request domain ──────────────────────────────────────────────────


class _RequestEventBase(BaseModel):
    """Shared shape for client-request lifecycle events.

    Emitted by ``api/controllers/requests.py:_publish``.
    """

    model_config = PAYLOAD_CONFIG

    request_id: NotBlankStr
    client_id: NotBlankStr
    status: NotBlankStr


class WsRequestSubmittedPayload(_RequestEventBase):
    """Payload for ``request.submitted``."""

    event_type: Literal[WsEventType.REQUEST_SUBMITTED] = WsEventType.REQUEST_SUBMITTED


class WsRequestScopedPayload(_RequestEventBase):
    """Payload for ``request.scoped``."""

    event_type: Literal[WsEventType.REQUEST_SCOPED] = WsEventType.REQUEST_SCOPED


class WsRequestApprovedPayload(_RequestEventBase):
    """Payload for ``request.approved``."""

    event_type: Literal[WsEventType.REQUEST_APPROVED] = WsEventType.REQUEST_APPROVED


class WsRequestTaskCreatedPayload(_RequestEventBase):
    """Payload for ``request.task_created``.

    Emitted by the real work-entry path once the background pipeline
    drives an approved request to ``TASK_CREATED`` (the task is in
    the task engine and an agent has been dispatched). Status
    discriminates the terminal hop inside the same channel that
    carries the intermediate ``request.approved`` event. ``task_id``
    carries the spawned task's id so the frontend can link directly
    to it without a second ``GET /requests/{id}`` round-trip.
    """

    event_type: Literal[WsEventType.REQUEST_TASK_CREATED] = (
        WsEventType.REQUEST_TASK_CREATED
    )
    task_id: NotBlankStr


class WsRequestRejectedPayload(_RequestEventBase):
    """Payload for ``request.rejected`` -- not yet emitted by Python."""

    event_type: Literal[WsEventType.REQUEST_REJECTED] = WsEventType.REQUEST_REJECTED


class WsRequestStatusChangedPayload(_RequestEventBase):
    """Payload for ``request.status_changed`` -- not yet emitted by Python."""

    event_type: Literal[WsEventType.REQUEST_STATUS_CHANGED] = (
        WsEventType.REQUEST_STATUS_CHANGED
    )
    previous_status: NotBlankStr | None = None


# ── Review domain ───────────────────────────────────────────────────


class WsReviewStageCompletedPayload(BaseModel):
    """Payload for ``review.stage_completed`` -- not yet emitted."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.REVIEW_STAGE_COMPLETED] = (
        WsEventType.REVIEW_STAGE_COMPLETED
    )
    task_id: NotBlankStr
    stage_name: NotBlankStr


class WsReviewStageDecidedPayload(BaseModel):
    """Payload for ``review.stage_decided``.

    Emitted by ``api/controllers/reviews.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.REVIEW_STAGE_DECIDED] = (
        WsEventType.REVIEW_STAGE_DECIDED
    )
    task_id: NotBlankStr
    stage_name: NotBlankStr
    verdict: NotBlankStr
    decided_by: NotBlankStr


class WsReviewPipelineCompletedPayload(BaseModel):
    """Payload for ``review.pipeline_completed`` -- not yet emitted."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.REVIEW_PIPELINE_COMPLETED] = (
        WsEventType.REVIEW_PIPELINE_COMPLETED
    )
    task_id: NotBlankStr
    final_verdict: NotBlankStr | None = None


# ── Simulation domain ───────────────────────────────────────────────


class _SimulationEventBase(BaseModel):
    """Shared shape for simulation lifecycle events.

    Emitted by ``api/controllers/simulations.py:_publish_event`` from a
    :class:`SimulationRecord`.
    """

    model_config = PAYLOAD_CONFIG

    simulation_id: NotBlankStr
    status: NotBlankStr
    progress: float = Field(ge=0.0, le=1.0)


class WsSimulationStartedPayload(_SimulationEventBase):
    """Payload for ``simulation.started``."""

    event_type: Literal[WsEventType.SIMULATION_STARTED] = WsEventType.SIMULATION_STARTED


class WsSimulationRunningPayload(_SimulationEventBase):
    """Payload for ``simulation.running`` -- not yet emitted by Python."""

    event_type: Literal[WsEventType.SIMULATION_RUNNING] = WsEventType.SIMULATION_RUNNING


class WsSimulationPausedPayload(_SimulationEventBase):
    """Payload for ``simulation.paused`` -- not yet emitted by Python."""

    event_type: Literal[WsEventType.SIMULATION_PAUSED] = WsEventType.SIMULATION_PAUSED


class WsSimulationCancelledPayload(_SimulationEventBase):
    """Payload for ``simulation.cancelled``."""

    event_type: Literal[WsEventType.SIMULATION_CANCELLED] = (
        WsEventType.SIMULATION_CANCELLED
    )


class WsSimulationCompletedPayload(_SimulationEventBase):
    """Payload for ``simulation.completed``."""

    event_type: Literal[WsEventType.SIMULATION_COMPLETED] = (
        WsEventType.SIMULATION_COMPLETED
    )


class WsSimulationFailedPayload(_SimulationEventBase):
    """Payload for ``simulation.failed``."""

    event_type: Literal[WsEventType.SIMULATION_FAILED] = WsEventType.SIMULATION_FAILED
    error: str | None = None


# ── Interrupt / dissent domain ──────────────────────────────────────


class WsInterruptCreatedPayload(BaseModel):
    """Payload for ``interrupt.created`` -- not yet emitted by Python."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.INTERRUPT_CREATED] = WsEventType.INTERRUPT_CREATED
    interrupt_id: NotBlankStr
    task_id: NotBlankStr
    reason: NotBlankStr | None = None


class WsInterruptResumedPayload(BaseModel):
    """Payload for ``interrupt.resumed`` -- not yet emitted by Python."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.INTERRUPT_RESUMED] = WsEventType.INTERRUPT_RESUMED
    interrupt_id: NotBlankStr
    task_id: NotBlankStr


class WsDissentPublishedPayload(BaseModel):
    """Payload for ``dissent.published`` -- not yet emitted by Python."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DISSENT_PUBLISHED] = WsEventType.DISSENT_PUBLISHED
    task_id: NotBlankStr
    agent_id: NotBlankStr
    message: NotBlankStr
