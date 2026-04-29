"""Typed payload models for WebSocket events.

Each :class:`~synthorg.api.ws_models.WsEventType` value has a frozen
Pydantic model carrying an ``event_type`` ``Literal`` discriminator.
:data:`WsEventPayload` is the discriminated union enforced at
``WsEvent`` construction (see ``WsEvent._validate_payload_shape``);
manual ``payload.get(...)`` walks at the consumer boundary go away
because every emit site is now structurally validated.

Maintainer notes:

* Models mirror the actual payload shapes constructed at emit sites
  under ``src/synthorg/api/`` (controllers + helpers + bridges) and
  ``src/synthorg/hr/``. The actual emitter is the source of truth; if
  a model and an emitter disagree, fix the model to match emit
  reality, then verify the frontend consumer still reads the same
  fields.
* For events declared in :class:`WsEventType` but not yet wired by
  any Python emitter (~17 stub variants today, e.g. some HR /
  scaling / review events), the model defines the minimum shape the
  frontend handler expects. These variants are unreachable from
  Python today; the wire validator only fires on emit. Tightening
  these models happens when the corresponding emitter lands.
"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
)

from synthorg.api.ws_models import WsEventType
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

# ── Base config ─────────────────────────────────────────────────────


_PAYLOAD_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


# ── Task domain ─────────────────────────────────────────────────────


class WsTaskCreatedPayload(BaseModel):
    """Payload for ``task.created`` -- a new task has been created."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_CREATED] = WsEventType.TASK_CREATED
    task_id: NotBlankStr
    title: NotBlankStr
    status: NotBlankStr
    assigned_agent_id: NotBlankStr | None = None
    project_id: NotBlankStr | None = None


class WsTaskUpdatedPayload(BaseModel):
    """Payload for ``task.updated`` -- task fields have changed."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_UPDATED] = WsEventType.TASK_UPDATED
    task_id: NotBlankStr
    title: NotBlankStr | None = None
    status: NotBlankStr | None = None
    assigned_agent_id: NotBlankStr | None = None


class WsTaskStatusChangedPayload(BaseModel):
    """Payload for ``task.status_changed`` -- task transitioned states."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_STATUS_CHANGED] = (
        WsEventType.TASK_STATUS_CHANGED
    )
    task_id: NotBlankStr
    from_status: NotBlankStr | None = None
    to_status: NotBlankStr


class WsTaskAssignedPayload(BaseModel):
    """Payload for ``task.assigned`` -- task assigned to an agent."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_ASSIGNED] = WsEventType.TASK_ASSIGNED
    task_id: NotBlankStr
    agent_id: NotBlankStr


# ── Agent domain ────────────────────────────────────────────────────


class WsAgentCreatedPayload(BaseModel):
    """Payload for ``agent.created`` -- agent added to the org config.

    Emitted by ``create_agent`` in ``api/controllers/agents.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_CREATED] = WsEventType.AGENT_CREATED
    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr


class WsAgentUpdatedPayload(BaseModel):
    """Payload for ``agent.updated`` -- agent config changed.

    Emitted by ``update_agent`` in ``api/controllers/agents.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_UPDATED] = WsEventType.AGENT_UPDATED
    name: NotBlankStr
    department: NotBlankStr


class WsAgentDeletedPayload(BaseModel):
    """Payload for ``agent.deleted`` -- agent removed from the org config.

    Emitted by ``delete_agent`` in ``api/controllers/agents.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_DELETED] = WsEventType.AGENT_DELETED
    name: NotBlankStr


class WsAgentHiredPayload(BaseModel):
    """Payload for ``agent.hired`` -- a hire decision landed."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_HIRED] = WsEventType.AGENT_HIRED
    agent_id: NotBlankStr
    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr


class WsAgentFiredPayload(BaseModel):
    """Payload for ``agent.fired`` -- a fire/prune decision landed."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_FIRED] = WsEventType.AGENT_FIRED
    agent_id: NotBlankStr
    name: NotBlankStr
    reason: str | None = None


class WsAgentStatusChangedPayload(BaseModel):
    """Payload for ``agent.status_changed`` -- agent runtime state shift."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_STATUS_CHANGED] = (
        WsEventType.AGENT_STATUS_CHANGED
    )
    agent_id: NotBlankStr
    from_status: NotBlankStr | None = None
    to_status: NotBlankStr


class WsAgentsReorderedPayload(BaseModel):
    """Payload for ``agents.reordered`` -- agent display order changed.

    Emitted by ``api/controllers/departments.py`` ``reorder_agents``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENTS_REORDERED] = WsEventType.AGENTS_REORDERED
    department: NotBlankStr | None = None
    agent_names: tuple[NotBlankStr, ...]


# ── Company / department domain ─────────────────────────────────────


class WsCompanyUpdatedPayload(BaseModel):
    """Payload for ``company.updated`` -- company-level config changed.

    Emitted by ``api/controllers/company.py`` ``update_company``;
    carries the subset of fields the caller actually mutated.  Field
    names match the keys built in
    ``OrgMutationService._apply_company_scalars``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COMPANY_UPDATED] = WsEventType.COMPANY_UPDATED
    company_name: NotBlankStr | None = None
    autonomy_level: NotBlankStr | None = None
    budget_monthly: float | None = Field(default=None, ge=0)
    communication_pattern: NotBlankStr | None = None


class WsDepartmentCreatedPayload(BaseModel):
    """Payload for ``department.created``.

    Emitted by ``api/controllers/departments.py`` ``create_department``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENT_CREATED] = WsEventType.DEPARTMENT_CREATED
    name: NotBlankStr
    description: str | None = None
    budget_percent: float | None = Field(default=None, ge=0, le=100)


class WsDepartmentUpdatedPayload(BaseModel):
    """Payload for ``department.updated``."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENT_UPDATED] = WsEventType.DEPARTMENT_UPDATED
    name: NotBlankStr
    description: str | None = None


class WsDepartmentDeletedPayload(BaseModel):
    """Payload for ``department.deleted``."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENT_DELETED] = WsEventType.DEPARTMENT_DELETED
    name: NotBlankStr


class WsDepartmentsReorderedPayload(BaseModel):
    """Payload for ``departments.reordered`` -- display order changed.

    Emitted by ``api/controllers/company.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENTS_REORDERED] = (
        WsEventType.DEPARTMENTS_REORDERED
    )
    department_names: tuple[NotBlankStr, ...]


# ── Personality domain ──────────────────────────────────────────────


class WsPersonalityTrimmedPayload(BaseModel):
    """Payload for ``personality.trimmed`` -- engine pruned a persona.

    Emitted by ``make_personality_trim_notifier`` in
    ``api/app_helpers.py``; field shape mirrors
    ``synthorg.engine.agent_engine.PersonalityTrimPayload``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.PERSONALITY_TRIMMED] = (
        WsEventType.PERSONALITY_TRIMMED
    )
    agent_id: NotBlankStr
    agent_name: NotBlankStr
    task_id: NotBlankStr
    trim_tier: Literal[1, 2, 3]
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    max_tokens: int = Field(ge=0)
    budget_met: bool


# ── Budget domain ───────────────────────────────────────────────────


class WsBudgetRecordAddedPayload(BaseModel):
    """Payload for ``budget.record_added`` -- a cost record was logged."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.BUDGET_RECORD_ADDED] = (
        WsEventType.BUDGET_RECORD_ADDED
    )
    amount: float
    currency: NotBlankStr
    category: NotBlankStr | None = None
    agent_id: NotBlankStr | None = None


class WsBudgetAlertPayload(BaseModel):
    """Payload for ``budget.alert`` -- threshold breached."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.BUDGET_ALERT] = WsEventType.BUDGET_ALERT
    severity: NotBlankStr
    message: NotBlankStr
    threshold: float | None = None
    current: float | None = None
    currency: NotBlankStr


# ── Message domain ──────────────────────────────────────────────────


class WsMessageSentPayload(BaseModel):
    """Payload for ``message.sent`` -- new message on the bus.

    Emitted by ``_to_ws_event`` in ``api/bus_bridge.py``. ``parts``
    carries the per-part dump of every typed ``Part`` variant so the
    frontend renders text + data + file/uri parts directly.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.MESSAGE_SENT] = WsEventType.MESSAGE_SENT
    message_id: NotBlankStr
    sender: NotBlankStr
    to: NotBlankStr
    content: str
    parts: tuple[dict[str, object], ...]


# ── System domain ───────────────────────────────────────────────────


class WsSystemErrorPayload(BaseModel):
    """Payload for ``system.error`` -- platform-level error."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.SYSTEM_ERROR] = WsEventType.SYSTEM_ERROR
    message: NotBlankStr
    code: NotBlankStr | None = None


class WsSystemStartupPayload(BaseModel):
    """Payload for ``system.startup`` -- platform booted."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.SYSTEM_STARTUP] = WsEventType.SYSTEM_STARTUP
    version: NotBlankStr | None = None


class WsSystemShutdownPayload(BaseModel):
    """Payload for ``system.shutdown`` -- platform shutting down."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.SYSTEM_SHUTDOWN] = WsEventType.SYSTEM_SHUTDOWN
    reason: str | None = None


# ── Approval domain ─────────────────────────────────────────────────


class _ApprovalEventBase(BaseModel):
    """Shared shape for approval lifecycle events.

    All four approval events carry the same wire payload: only the
    ``event_type`` discriminator differs.  Each variant subclasses this
    base and pins the ``event_type`` literal.
    """

    model_config = _PAYLOAD_CONFIG

    approval_id: NotBlankStr
    status: NotBlankStr
    action_type: NotBlankStr
    risk_level: NotBlankStr


class WsApprovalSubmittedPayload(_ApprovalEventBase):
    """Payload for ``approval.submitted``."""

    event_type: Literal[WsEventType.APPROVAL_SUBMITTED] = WsEventType.APPROVAL_SUBMITTED


class WsApprovalApprovedPayload(_ApprovalEventBase):
    """Payload for ``approval.approved``."""

    event_type: Literal[WsEventType.APPROVAL_APPROVED] = WsEventType.APPROVAL_APPROVED


class WsApprovalRejectedPayload(_ApprovalEventBase):
    """Payload for ``approval.rejected``."""

    event_type: Literal[WsEventType.APPROVAL_REJECTED] = WsEventType.APPROVAL_REJECTED


class WsApprovalExpiredPayload(_ApprovalEventBase):
    """Payload for ``approval.expired`` -- lazy expiry transition.

    Emitted by ``_make_expire_callback`` in ``api/app_helpers.py``.
    """

    event_type: Literal[WsEventType.APPROVAL_EXPIRED] = WsEventType.APPROVAL_EXPIRED


# ── Coordination domain ─────────────────────────────────────────────


class WsCoordinationStartedPayload(BaseModel):
    """Payload for ``coordination.started`` -- multi-agent run kicking off.

    Emitted by ``api/controllers/coordination.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_STARTED] = (
        WsEventType.COORDINATION_STARTED
    )
    task_id: NotBlankStr
    agent_count: int = Field(ge=0)


class WsCoordinationPhaseCompletedPayload(BaseModel):
    """Payload for ``coordination.phase_completed`` -- per-phase progress.

    Reserved for future per-phase events; not yet emitted.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_PHASE_COMPLETED] = (
        WsEventType.COORDINATION_PHASE_COMPLETED
    )
    task_id: NotBlankStr
    phase: NotBlankStr
    success: bool
    duration_seconds: float | None = None


class WsCoordinationCompletedPayload(BaseModel):
    """Payload for ``coordination.completed`` -- full run finished.

    Emitted by ``api/controllers/coordination.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_COMPLETED] = (
        WsEventType.COORDINATION_COMPLETED
    )
    task_id: NotBlankStr
    topology: NotBlankStr
    is_success: bool
    total_duration_seconds: float = Field(ge=0)


class WsCoordinationFailedPayload(BaseModel):
    """Payload for ``coordination.failed`` -- run failed (per-phase or overall).

    Emitted by multiple sites in ``api/controllers/coordination.py``
    with overlapping shapes; ``phase`` is set only on per-phase
    failures, ``topology`` only on full-run failures that reached
    topology resolution, ``error`` carries a client-safe message.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_FAILED] = (
        WsEventType.COORDINATION_FAILED
    )
    task_id: NotBlankStr
    phase: NotBlankStr | None = None
    topology: NotBlankStr | None = None
    is_success: bool | None = None
    total_duration_seconds: float | None = None
    error: str | None = None


# ── Meeting domain ──────────────────────────────────────────────────


class _MeetingEventBase(BaseModel):
    """Shared shape for meeting lifecycle events.

    Emitted via ``api/app_helpers.py:_make_meeting_publisher`` whose
    callback receives ``(event_name, payload)`` from the meeting
    orchestrator. The payload always identifies the meeting and the
    triggering event class; per-status detail fields are optional.
    """

    model_config = _PAYLOAD_CONFIG

    meeting_id: NotBlankStr
    meeting_type: NotBlankStr
    project_id: NotBlankStr | None = None
    department: NotBlankStr | None = None
    participants: tuple[NotBlankStr, ...] = ()


class WsMeetingStartedPayload(_MeetingEventBase):
    """Payload for ``meeting.started``."""

    event_type: Literal[WsEventType.MEETING_STARTED] = WsEventType.MEETING_STARTED


class WsMeetingCompletedPayload(_MeetingEventBase):
    """Payload for ``meeting.completed``.

    Carries optional summary fields populated by the orchestrator on
    success.
    """

    event_type: Literal[WsEventType.MEETING_COMPLETED] = WsEventType.MEETING_COMPLETED
    duration_seconds: float | None = None
    summary: str | None = None


class WsMeetingFailedPayload(_MeetingEventBase):
    """Payload for ``meeting.failed``.

    Mirrored by ``communication/meeting/scheduler.py``'s
    ``_STATUS_TO_WS_EVENT`` for both ``failed`` and ``budget_exhausted``
    terminal states.
    """

    event_type: Literal[WsEventType.MEETING_FAILED] = WsEventType.MEETING_FAILED
    error: str | None = None
    reason: NotBlankStr | None = None


# ── Artifact domain ─────────────────────────────────────────────────


class WsArtifactCreatedPayload(BaseModel):
    """Payload for ``artifact.created``.

    Emitted by ``api/controllers/artifacts.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.ARTIFACT_CREATED] = WsEventType.ARTIFACT_CREATED
    artifact_id: NotBlankStr
    task_id: NotBlankStr
    created_by: NotBlankStr
    type: NotBlankStr


class WsArtifactDeletedPayload(BaseModel):
    """Payload for ``artifact.deleted``.

    Emitted by ``api/controllers/artifacts.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.ARTIFACT_DELETED] = WsEventType.ARTIFACT_DELETED
    artifact_id: NotBlankStr
    task_id: NotBlankStr


class WsArtifactContentUploadedPayload(BaseModel):
    """Payload for ``artifact.content_uploaded``.

    Emitted by ``api/controllers/artifacts.py``.
    """

    model_config = _PAYLOAD_CONFIG

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

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.PROJECT_CREATED] = WsEventType.PROJECT_CREATED
    project_id: NotBlankStr
    name: NotBlankStr
    status: NotBlankStr
    lead: NotBlankStr | None = None


class WsProjectDeletedPayload(BaseModel):
    """Payload for ``project.deleted``.

    Emitted by ``api/controllers/projects.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.PROJECT_DELETED] = WsEventType.PROJECT_DELETED
    project_id: NotBlankStr
    name: NotBlankStr


class WsProjectStatusChangedPayload(BaseModel):
    """Payload for ``project.status_changed``.

    Reserved for the future project-status endpoint; not yet emitted
    by Python.
    """

    model_config = _PAYLOAD_CONFIG

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

    model_config = _PAYLOAD_CONFIG

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

    model_config = _PAYLOAD_CONFIG

    client_id: NotBlankStr
    name: NotBlankStr
    strictness_level: float = Field(ge=0.0)


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

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.CLIENT_DELETED] = WsEventType.CLIENT_DELETED
    client_id: NotBlankStr
    name: NotBlankStr | None = None


# ── Request domain ──────────────────────────────────────────────────


class _RequestEventBase(BaseModel):
    """Shared shape for client-request lifecycle events.

    Emitted by ``api/controllers/requests.py:_publish``.
    """

    model_config = _PAYLOAD_CONFIG

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

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.REVIEW_STAGE_COMPLETED] = (
        WsEventType.REVIEW_STAGE_COMPLETED
    )
    task_id: NotBlankStr
    stage_name: NotBlankStr


class WsReviewStageDecidedPayload(BaseModel):
    """Payload for ``review.stage_decided``.

    Emitted by ``api/controllers/reviews.py``.
    """

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.REVIEW_STAGE_DECIDED] = (
        WsEventType.REVIEW_STAGE_DECIDED
    )
    task_id: NotBlankStr
    stage_name: NotBlankStr
    verdict: NotBlankStr
    decided_by: NotBlankStr


class WsReviewPipelineCompletedPayload(BaseModel):
    """Payload for ``review.pipeline_completed`` -- not yet emitted."""

    model_config = _PAYLOAD_CONFIG

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

    model_config = _PAYLOAD_CONFIG

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

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.INTERRUPT_CREATED] = WsEventType.INTERRUPT_CREATED
    interrupt_id: NotBlankStr
    task_id: NotBlankStr
    reason: NotBlankStr | None = None


class WsInterruptResumedPayload(BaseModel):
    """Payload for ``interrupt.resumed`` -- not yet emitted by Python."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.INTERRUPT_RESUMED] = WsEventType.INTERRUPT_RESUMED
    interrupt_id: NotBlankStr
    task_id: NotBlankStr


class WsDissentPublishedPayload(BaseModel):
    """Payload for ``dissent.published`` -- not yet emitted by Python."""

    model_config = _PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DISSENT_PUBLISHED] = WsEventType.DISSENT_PUBLISHED
    task_id: NotBlankStr
    agent_id: NotBlankStr
    message: NotBlankStr


# ── Discriminated union ─────────────────────────────────────────────


WsEventPayload = Annotated[
    WsTaskCreatedPayload
    | WsTaskUpdatedPayload
    | WsTaskStatusChangedPayload
    | WsTaskAssignedPayload
    | WsAgentCreatedPayload
    | WsAgentUpdatedPayload
    | WsAgentDeletedPayload
    | WsAgentHiredPayload
    | WsAgentFiredPayload
    | WsAgentStatusChangedPayload
    | WsAgentsReorderedPayload
    | WsCompanyUpdatedPayload
    | WsDepartmentCreatedPayload
    | WsDepartmentUpdatedPayload
    | WsDepartmentDeletedPayload
    | WsDepartmentsReorderedPayload
    | WsPersonalityTrimmedPayload
    | WsBudgetRecordAddedPayload
    | WsBudgetAlertPayload
    | WsMessageSentPayload
    | WsSystemErrorPayload
    | WsSystemStartupPayload
    | WsSystemShutdownPayload
    | WsApprovalSubmittedPayload
    | WsApprovalApprovedPayload
    | WsApprovalRejectedPayload
    | WsApprovalExpiredPayload
    | WsCoordinationStartedPayload
    | WsCoordinationPhaseCompletedPayload
    | WsCoordinationCompletedPayload
    | WsCoordinationFailedPayload
    | WsMeetingStartedPayload
    | WsMeetingCompletedPayload
    | WsMeetingFailedPayload
    | WsArtifactCreatedPayload
    | WsArtifactDeletedPayload
    | WsArtifactContentUploadedPayload
    | WsProjectCreatedPayload
    | WsProjectDeletedPayload
    | WsProjectStatusChangedPayload
    | WsMemoryFineTuneProgressPayload
    | WsMemoryFineTuneStageChangedPayload
    | WsMemoryFineTuneCompletedPayload
    | WsMemoryFineTuneFailedPayload
    | WsClientCreatedPayload
    | WsClientUpdatedPayload
    | WsClientDeactivatedPayload
    | WsClientDeletedPayload
    | WsRequestSubmittedPayload
    | WsRequestScopedPayload
    | WsRequestApprovedPayload
    | WsRequestRejectedPayload
    | WsRequestStatusChangedPayload
    | WsReviewStageCompletedPayload
    | WsReviewStageDecidedPayload
    | WsReviewPipelineCompletedPayload
    | WsSimulationStartedPayload
    | WsSimulationRunningPayload
    | WsSimulationPausedPayload
    | WsSimulationCancelledPayload
    | WsSimulationCompletedPayload
    | WsSimulationFailedPayload
    | WsInterruptCreatedPayload
    | WsInterruptResumedPayload
    | WsDissentPublishedPayload,
    Discriminator("event_type"),
]
"""Discriminated union of every typed WebSocket event payload.

Pydantic uses the ``event_type`` literal on each variant to deserialize
into the correct typed model.  The union is exhaustive over
:class:`~synthorg.api.ws_models.WsEventType`; an integration test in
``tests/unit/api/test_ws_payloads.py`` enforces parity between the enum
and the union variants.
"""


__all__ = [
    "WsAgentCreatedPayload",
    "WsAgentDeletedPayload",
    "WsAgentFiredPayload",
    "WsAgentHiredPayload",
    "WsAgentStatusChangedPayload",
    "WsAgentUpdatedPayload",
    "WsAgentsReorderedPayload",
    "WsApprovalApprovedPayload",
    "WsApprovalExpiredPayload",
    "WsApprovalRejectedPayload",
    "WsApprovalSubmittedPayload",
    "WsArtifactContentUploadedPayload",
    "WsArtifactCreatedPayload",
    "WsArtifactDeletedPayload",
    "WsBudgetAlertPayload",
    "WsBudgetRecordAddedPayload",
    "WsClientCreatedPayload",
    "WsClientDeactivatedPayload",
    "WsClientDeletedPayload",
    "WsClientUpdatedPayload",
    "WsCompanyUpdatedPayload",
    "WsCoordinationCompletedPayload",
    "WsCoordinationFailedPayload",
    "WsCoordinationPhaseCompletedPayload",
    "WsCoordinationStartedPayload",
    "WsDepartmentCreatedPayload",
    "WsDepartmentDeletedPayload",
    "WsDepartmentUpdatedPayload",
    "WsDepartmentsReorderedPayload",
    "WsDissentPublishedPayload",
    "WsEventPayload",
    "WsInterruptCreatedPayload",
    "WsInterruptResumedPayload",
    "WsMeetingCompletedPayload",
    "WsMeetingFailedPayload",
    "WsMeetingStartedPayload",
    "WsMemoryFineTuneCompletedPayload",
    "WsMemoryFineTuneFailedPayload",
    "WsMemoryFineTuneProgressPayload",
    "WsMemoryFineTuneStageChangedPayload",
    "WsMessageSentPayload",
    "WsPersonalityTrimmedPayload",
    "WsProjectCreatedPayload",
    "WsProjectDeletedPayload",
    "WsProjectStatusChangedPayload",
    "WsRequestApprovedPayload",
    "WsRequestRejectedPayload",
    "WsRequestScopedPayload",
    "WsRequestStatusChangedPayload",
    "WsRequestSubmittedPayload",
    "WsReviewPipelineCompletedPayload",
    "WsReviewStageCompletedPayload",
    "WsReviewStageDecidedPayload",
    "WsSimulationCancelledPayload",
    "WsSimulationCompletedPayload",
    "WsSimulationFailedPayload",
    "WsSimulationPausedPayload",
    "WsSimulationRunningPayload",
    "WsSimulationStartedPayload",
    "WsSystemErrorPayload",
    "WsSystemShutdownPayload",
    "WsSystemStartupPayload",
    "WsTaskAssignedPayload",
    "WsTaskCreatedPayload",
    "WsTaskStatusChangedPayload",
    "WsTaskUpdatedPayload",
]
