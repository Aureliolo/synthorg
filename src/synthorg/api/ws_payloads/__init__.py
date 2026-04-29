"""Typed payload models for WebSocket events.

Each :class:`~synthorg.api.ws_models.WsEventType` value has a frozen
Pydantic model carrying an ``event_type`` ``Literal`` discriminator.
:data:`WsEventPayload` is the discriminated union enforced at
``WsEvent`` construction (see ``WsEvent._validate_payload_shape``);
manual ``payload.get(...)`` walks at the consumer boundary go away
because every emit site is now structurally validated.

Models are split across two submodules to keep each file under the
800-line guideline:

* :mod:`._lifecycle` -- task, agent, company, budget, message, system,
  approval, coordination, meeting events.
* :mod:`._domain` -- artifact, project, memory fine-tune, client,
  request, review, simulation, interrupt/dissent events.

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

from typing import Annotated

from pydantic import Discriminator

from synthorg.api.ws_payloads._domain import (
    WsArtifactContentUploadedPayload,
    WsArtifactCreatedPayload,
    WsArtifactDeletedPayload,
    WsClientCreatedPayload,
    WsClientDeactivatedPayload,
    WsClientDeletedPayload,
    WsClientUpdatedPayload,
    WsDissentPublishedPayload,
    WsInterruptCreatedPayload,
    WsInterruptResumedPayload,
    WsMemoryFineTuneCompletedPayload,
    WsMemoryFineTuneFailedPayload,
    WsMemoryFineTuneProgressPayload,
    WsMemoryFineTuneStageChangedPayload,
    WsProjectCreatedPayload,
    WsProjectDeletedPayload,
    WsProjectStatusChangedPayload,
    WsRequestApprovedPayload,
    WsRequestRejectedPayload,
    WsRequestScopedPayload,
    WsRequestStatusChangedPayload,
    WsRequestSubmittedPayload,
    WsReviewPipelineCompletedPayload,
    WsReviewStageCompletedPayload,
    WsReviewStageDecidedPayload,
    WsSimulationCancelledPayload,
    WsSimulationCompletedPayload,
    WsSimulationFailedPayload,
    WsSimulationPausedPayload,
    WsSimulationRunningPayload,
    WsSimulationStartedPayload,
)
from synthorg.api.ws_payloads._lifecycle import (
    WsAgentCreatedPayload,
    WsAgentDeletedPayload,
    WsAgentFiredPayload,
    WsAgentHiredPayload,
    WsAgentsReorderedPayload,
    WsAgentStatusChangedPayload,
    WsAgentUpdatedPayload,
    WsApprovalApprovedPayload,
    WsApprovalExpiredPayload,
    WsApprovalRejectedPayload,
    WsApprovalSubmittedPayload,
    WsBudgetAlertPayload,
    WsBudgetRecordAddedPayload,
    WsCompanyUpdatedPayload,
    WsCoordinationCompletedPayload,
    WsCoordinationFailedPayload,
    WsCoordinationPhaseCompletedPayload,
    WsCoordinationStartedPayload,
    WsDepartmentCreatedPayload,
    WsDepartmentDeletedPayload,
    WsDepartmentsReorderedPayload,
    WsDepartmentUpdatedPayload,
    WsMeetingCompletedPayload,
    WsMeetingFailedPayload,
    WsMeetingStartedPayload,
    WsMessageSentPayload,
    WsPersonalityTrimmedPayload,
    WsSystemErrorPayload,
    WsSystemShutdownPayload,
    WsSystemStartupPayload,
    WsTaskAssignedPayload,
    WsTaskCreatedPayload,
    WsTaskStatusChangedPayload,
    WsTaskUpdatedPayload,
)

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
