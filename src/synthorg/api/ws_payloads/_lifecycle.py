"""Lifecycle event payloads.

Covers task, agent, company, budget, message, system, approval,
coordination, meeting.  The first half of the WebSocket
discriminated-union surface; see ``synthorg.api.ws_payloads.__init__``
for the union + re-exports.
"""

from typing import Literal

from pydantic import BaseModel, Field

from synthorg.api.ws_models import WsEventType
from synthorg.api.ws_payloads._base import PAYLOAD_CONFIG
from synthorg.budget.currency import CurrencyCode
from synthorg.communication.message import Part
from synthorg.core.types import NotBlankStr

# ── Task domain ─────────────────────────────────────────────────────


class WsTaskCreatedPayload(BaseModel):
    """Payload for ``task.created`` -- a new task has been created."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_CREATED] = WsEventType.TASK_CREATED
    task_id: NotBlankStr
    title: NotBlankStr
    status: NotBlankStr
    assigned_agent_id: NotBlankStr | None = None
    project_id: NotBlankStr | None = None


class WsTaskUpdatedPayload(BaseModel):
    """Payload for ``task.updated`` -- task fields have changed."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_UPDATED] = WsEventType.TASK_UPDATED
    task_id: NotBlankStr
    title: NotBlankStr | None = None
    status: NotBlankStr | None = None
    assigned_agent_id: NotBlankStr | None = None


class WsTaskStatusChangedPayload(BaseModel):
    """Payload for ``task.status_changed`` -- task transitioned states."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_STATUS_CHANGED] = (
        WsEventType.TASK_STATUS_CHANGED
    )
    task_id: NotBlankStr
    from_status: NotBlankStr | None = None
    to_status: NotBlankStr


class WsTaskAssignedPayload(BaseModel):
    """Payload for ``task.assigned`` -- task assigned to an agent."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.TASK_ASSIGNED] = WsEventType.TASK_ASSIGNED
    task_id: NotBlankStr
    agent_id: NotBlankStr


# ── Agent domain ────────────────────────────────────────────────────


class WsAgentCreatedPayload(BaseModel):
    """Payload for ``agent.created`` -- agent added to the org config.

    Emitted by ``create_agent`` in ``api/controllers/agents.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_CREATED] = WsEventType.AGENT_CREATED
    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr


class WsAgentUpdatedPayload(BaseModel):
    """Payload for ``agent.updated`` -- agent config changed.

    Emitted by ``update_agent`` in ``api/controllers/agents.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_UPDATED] = WsEventType.AGENT_UPDATED
    name: NotBlankStr
    department: NotBlankStr


class WsAgentDeletedPayload(BaseModel):
    """Payload for ``agent.deleted`` -- agent removed from the org config.

    Emitted by ``delete_agent`` in ``api/controllers/agents.py``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_DELETED] = WsEventType.AGENT_DELETED
    name: NotBlankStr


class WsAgentHiredPayload(BaseModel):
    """Payload for ``agent.hired`` -- a hire decision landed."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_HIRED] = WsEventType.AGENT_HIRED
    agent_id: NotBlankStr
    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr


class WsAgentFiredPayload(BaseModel):
    """Payload for ``agent.fired`` -- a fire/prune decision landed."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.AGENT_FIRED] = WsEventType.AGENT_FIRED
    agent_id: NotBlankStr
    name: NotBlankStr
    reason: str | None = None


class WsAgentStatusChangedPayload(BaseModel):
    """Payload for ``agent.status_changed`` -- agent runtime state shift."""

    model_config = PAYLOAD_CONFIG

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

    model_config = PAYLOAD_CONFIG

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

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COMPANY_UPDATED] = WsEventType.COMPANY_UPDATED
    company_name: NotBlankStr | None = None
    autonomy_level: NotBlankStr | None = None
    budget_monthly: float | None = Field(default=None, ge=0)
    communication_pattern: NotBlankStr | None = None


class WsDepartmentCreatedPayload(BaseModel):
    """Payload for ``department.created``.

    Emitted by ``api/controllers/departments.py`` ``create_department``.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENT_CREATED] = WsEventType.DEPARTMENT_CREATED
    name: NotBlankStr
    description: str | None = None
    budget_percent: float | None = Field(default=None, ge=0, le=100)


class WsDepartmentUpdatedPayload(BaseModel):
    """Payload for ``department.updated``."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENT_UPDATED] = WsEventType.DEPARTMENT_UPDATED
    name: NotBlankStr
    description: str | None = None


class WsDepartmentDeletedPayload(BaseModel):
    """Payload for ``department.deleted``."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.DEPARTMENT_DELETED] = WsEventType.DEPARTMENT_DELETED
    name: NotBlankStr


class WsDepartmentsReorderedPayload(BaseModel):
    """Payload for ``departments.reordered`` -- display order changed.

    Emitted by ``api/controllers/company.py``.
    """

    model_config = PAYLOAD_CONFIG

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

    model_config = PAYLOAD_CONFIG

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

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.BUDGET_RECORD_ADDED] = (
        WsEventType.BUDGET_RECORD_ADDED
    )
    amount: float = Field(ge=0, description="Cost amount (non-negative)")
    currency: CurrencyCode
    category: NotBlankStr | None = None
    agent_id: NotBlankStr | None = None


class WsBudgetAlertPayload(BaseModel):
    """Payload for ``budget.alert`` -- threshold breached."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.BUDGET_ALERT] = WsEventType.BUDGET_ALERT
    severity: NotBlankStr
    message: NotBlankStr
    threshold: float | None = Field(default=None, ge=0)
    current: float | None = Field(default=None, ge=0)
    currency: CurrencyCode


# ── Message domain ──────────────────────────────────────────────────


class WsMessageSentPayload(BaseModel):
    """Payload for ``message.sent`` -- new message on the bus.

    Emitted by ``_to_ws_event`` in ``api/bus_bridge.py``. ``parts``
    carries the per-part dump of every typed ``Part`` variant so the
    frontend renders text + data + file/uri parts directly.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.MESSAGE_SENT] = WsEventType.MESSAGE_SENT
    message_id: NotBlankStr
    sender: NotBlankStr
    to: NotBlankStr
    content: str
    parts: tuple[Part, ...] = Field(
        default=(),
        description=(
            "Typed message parts (text/data/file/uri) discriminated by ``type``"
        ),
    )


# ── System domain ───────────────────────────────────────────────────


class WsSystemErrorPayload(BaseModel):
    """Payload for ``system.error`` -- platform-level error."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.SYSTEM_ERROR] = WsEventType.SYSTEM_ERROR
    message: NotBlankStr
    code: NotBlankStr | None = None


class WsSystemStartupPayload(BaseModel):
    """Payload for ``system.startup`` -- platform booted."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.SYSTEM_STARTUP] = WsEventType.SYSTEM_STARTUP
    version: NotBlankStr | None = None


class WsSystemShutdownPayload(BaseModel):
    """Payload for ``system.shutdown`` -- platform shutting down."""

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.SYSTEM_SHUTDOWN] = WsEventType.SYSTEM_SHUTDOWN
    reason: str | None = None


# ── Approval domain ─────────────────────────────────────────────────


class _ApprovalEventBase(BaseModel):
    """Shared shape for approval lifecycle events.

    All four approval events carry the same wire payload: only the
    ``event_type`` discriminator differs.  Each variant subclasses this
    base and pins the ``event_type`` literal.
    """

    model_config = PAYLOAD_CONFIG

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

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_STARTED] = (
        WsEventType.COORDINATION_STARTED
    )
    task_id: NotBlankStr
    agent_count: int = Field(ge=0)


class WsCoordinationPhaseCompletedPayload(BaseModel):
    """Payload for ``coordination.phase_completed`` -- per-phase progress.

    Reserved for future per-phase events; not yet emitted.
    """

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_PHASE_COMPLETED] = (
        WsEventType.COORDINATION_PHASE_COMPLETED
    )
    task_id: NotBlankStr
    phase: NotBlankStr
    success: bool
    duration_seconds: float | None = Field(default=None, ge=0)


class WsCoordinationCompletedPayload(BaseModel):
    """Payload for ``coordination.completed`` -- full run finished.

    Emitted by ``api/controllers/coordination.py``.
    """

    model_config = PAYLOAD_CONFIG

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

    model_config = PAYLOAD_CONFIG

    event_type: Literal[WsEventType.COORDINATION_FAILED] = (
        WsEventType.COORDINATION_FAILED
    )
    task_id: NotBlankStr
    phase: NotBlankStr | None = None
    topology: NotBlankStr | None = None
    is_success: bool | None = None
    total_duration_seconds: float | None = Field(default=None, ge=0)
    error: str | None = None


# ── Meeting domain ──────────────────────────────────────────────────


class _MeetingEventBase(BaseModel):
    """Shared shape for meeting lifecycle events.

    Emitted via ``api/app_helpers.py:_make_meeting_publisher`` whose
    callback receives ``(event_name, payload)`` from the meeting
    orchestrator. The payload always identifies the meeting and the
    triggering event class; per-status detail fields are optional.
    """

    model_config = PAYLOAD_CONFIG

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
    duration_seconds: float | None = Field(default=None, ge=0)
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
