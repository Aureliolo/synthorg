/** WebSocket channel + event-type constants, base envelope, and subscription messages. */

import type {
  WsAgentCreatedPayload,
  WsAgentDeletedPayload,
  WsAgentFiredPayload,
  WsAgentHiredPayload,
  WsAgentStatusChangedPayload,
  WsAgentUpdatedPayload,
  WsAgentsReorderedPayload,
  WsCompanyUpdatedPayload,
  WsDepartmentCreatedPayload,
  WsDepartmentDeletedPayload,
  WsDepartmentUpdatedPayload,
  WsDepartmentsReorderedPayload,
  WsPersonalityTrimmedPayload,
  WsTaskAssignedPayload,
  WsTaskCreatedPayload,
  WsTaskStatusChangedPayload,
  WsTaskUpdatedPayload,
} from './task'
import type {
  WsApprovalEventPayload,
  WsCoordinationCompletedPayload,
  WsCoordinationFailedPayload,
  WsCoordinationPhaseCompletedPayload,
  WsCoordinationStartedPayload,
  WsMeetingCompletedPayload,
  WsMeetingFailedPayload,
  WsMeetingStartedPayload,
} from './approval'
import type {
  WsArtifactContentUploadedPayload,
  WsArtifactCreatedPayload,
  WsArtifactDeletedPayload,
  WsClientDeletedPayload,
  WsClientEventPayload,
  WsMemoryFineTuneEventPayload,
  WsMemoryFineTuneFailedPayload,
  WsMemoryFineTuneStageChangedPayload,
  WsPlanChangesRequestedPayload,
  WsPlanUpdatedPayload,
  WsProjectCreatedPayload,
  WsProjectDeletedPayload,
  WsProjectStatusChangedPayload,
  WsWorkflowExecutionStatusChangedPayload,
} from './provider'
import type {
  WsBudgetAlertPayload,
  WsBudgetRecordAddedPayload,
  WsMessageSentPayload,
  WsSystemErrorPayload,
  WsSystemShutdownPayload,
  WsSystemStartupPayload,
} from './system'
import type {
  WsDissentPublishedPayload,
  WsInterruptCreatedPayload,
  WsInterruptResumedPayload,
  WsRequestEventPayload,
  WsRequestStatusChangedPayload,
  WsRequestTaskCreatedPayload,
  WsReviewPipelineCompletedPayload,
  WsReviewStageCompletedPayload,
  WsReviewStageDecidedPayload,
  WsSimulationEventPayload,
  WsSimulationFailedPayload,
} from './request'
import type {
  WsSteeringDirectiveIssuedPayload,
  WsSteeringSupersessionProposedPayload,
  WsSteeringTasksSupersededPayload,
} from './cockpit'
import type { WsEventType } from '../backend-enums.gen'

// Synchronised with channel constants in `src/synthorg/api/channels.py`.
// Admin-only channels (#dissent, #webhooks, #ratelimit) are not exposed
// to dashboard subscribers; the user-scoped `user:{id}` channel is
// dynamic and matched by prefix server-side, not by name here. The
// `scaling` channel is reserved for future hr.scaling.* events; the
// dashboard's scaling page subscribes today, but no Python emitter
// publishes yet.
export const WS_CHANNELS = [
  'tasks', 'agents', 'budget', 'messages', 'system',
  'approvals', 'plans', 'meetings', 'artifacts', 'projects',
  'company', 'departments', 'clients', 'requests',
  'simulations', 'reviews', 'events', 'interrupts',
  'scaling', 'cockpit', 'workflows',
] as const

export type WsChannel = typeof WS_CHANNELS[number]

// `WS_EVENT_TYPE_VALUES` + `WsEventType` are generated from
// `WsEventType` in `src/synthorg/api/ws_models.py` by
// `scripts/generate_backend_enums_ts.py` (drift-gated at pre-push).
export { WS_EVENT_TYPE_VALUES } from '../backend-enums.gen'
export type { WsEventType }

export interface WsEvent {
  /**
   * Stable per-event id. The dashboard SSE fallback replays the recent
   * backlog on reconnect, so the client deduplicates by this id to dispatch
   * each event once. Optional: absent on legacy events (then not deduped).
   */
  event_id?: string
  /**
   * Wire-protocol version. Absent on legacy events: treated as 1.
   * Clients MUST ignore events whose version they do not understand.
   * Coordinates with `WS_PROTOCOL_VERSION` in `@/utils/constants` and
   * the server's `WsEvent.version` in `src/synthorg/api/ws_models.py`.
   */
  version?: number
  event_type: WsEventType
  channel: WsChannel
  timestamp: string
  payload: Record<string, unknown>
}

/**
 * **Sanitisation contract (MANDATORY)**: every string field in the
 * payload interfaces is attacker-reachable. The server forwards
 * strings the frontend cannot trust (third-party agent output,
 * untrusted user input, error messages from misbehaving providers).
 * Consumers MUST route every string through ``sanitizeWsString()``
 * before display or persistence, and through ``sanitizeWsEnum<T>()``
 * for any field whose type is a literal union or enum allowlist. The
 * TypeScript types declare structural shape only; they do NOT prove a
 * value has been clamped against C0 controls, bidi-overrides, length
 * caps, or the enum allowlist. Stores that ingest these payloads
 * (approvals, meetings, messages, tasks, etc.) own the sanitisation
 * step at the dispatch boundary.
 *
 * Map every {@link WsEventType} to its payload interface. The four
 * approval lifecycle events (`approval.submitted` / `approved` /
 * `rejected` / `expired`) and three meeting events share base shapes
 * mirrored from the Python ``_ApprovalEventBase`` / ``_MeetingEventBase``
 * helpers; the simulation, request, client, and memory.fine_tune
 * families do the same.
 *
 * Adding a new {@link WsEventType} member without an entry here is a
 * compile-time error via the exhaustiveness guards below: the primary
 * drift guard between the value tuple and the payload union.
 */
export interface WsEventPayloadMap {
  'task.created': WsTaskCreatedPayload
  'task.updated': WsTaskUpdatedPayload
  'task.status_changed': WsTaskStatusChangedPayload
  'task.assigned': WsTaskAssignedPayload
  'agent.hired': WsAgentHiredPayload
  'agent.fired': WsAgentFiredPayload
  'agent.status_changed': WsAgentStatusChangedPayload
  'agent.created': WsAgentCreatedPayload
  'agent.updated': WsAgentUpdatedPayload
  'agent.deleted': WsAgentDeletedPayload
  'agents.reordered': WsAgentsReorderedPayload
  'company.updated': WsCompanyUpdatedPayload
  'department.created': WsDepartmentCreatedPayload
  'department.updated': WsDepartmentUpdatedPayload
  'department.deleted': WsDepartmentDeletedPayload
  'departments.reordered': WsDepartmentsReorderedPayload
  'personality.trimmed': WsPersonalityTrimmedPayload
  'budget.record_added': WsBudgetRecordAddedPayload
  'budget.alert': WsBudgetAlertPayload
  'message.sent': WsMessageSentPayload
  'system.error': WsSystemErrorPayload
  'system.startup': WsSystemStartupPayload
  'system.shutdown': WsSystemShutdownPayload
  'approval.submitted': WsApprovalEventPayload
  'approval.approved': WsApprovalEventPayload
  'approval.rejected': WsApprovalEventPayload
  'approval.expired': WsApprovalEventPayload
  'coordination.started': WsCoordinationStartedPayload
  'coordination.phase_completed': WsCoordinationPhaseCompletedPayload
  'coordination.completed': WsCoordinationCompletedPayload
  'coordination.failed': WsCoordinationFailedPayload
  'meeting.started': WsMeetingStartedPayload
  'meeting.completed': WsMeetingCompletedPayload
  'meeting.failed': WsMeetingFailedPayload
  'artifact.created': WsArtifactCreatedPayload
  'artifact.deleted': WsArtifactDeletedPayload
  'artifact.content_uploaded': WsArtifactContentUploadedPayload
  'project.created': WsProjectCreatedPayload
  'project.deleted': WsProjectDeletedPayload
  'project.status_changed': WsProjectStatusChangedPayload
  'plan.updated': WsPlanUpdatedPayload
  'plan.changes_requested': WsPlanChangesRequestedPayload
  'workflow_execution.status_changed': WsWorkflowExecutionStatusChangedPayload
  'memory.fine_tune.progress': WsMemoryFineTuneEventPayload
  'memory.fine_tune.stage_changed': WsMemoryFineTuneStageChangedPayload
  'memory.fine_tune.completed': WsMemoryFineTuneEventPayload
  'memory.fine_tune.failed': WsMemoryFineTuneFailedPayload
  'client.created': WsClientEventPayload
  'client.updated': WsClientEventPayload
  'client.deactivated': WsClientEventPayload
  'client.deleted': WsClientDeletedPayload
  'request.submitted': WsRequestEventPayload
  'request.scoped': WsRequestEventPayload
  'request.approved': WsRequestEventPayload
  'request.task_created': WsRequestTaskCreatedPayload
  'request.rejected': WsRequestEventPayload
  'request.status_changed': WsRequestStatusChangedPayload
  'simulation.started': WsSimulationEventPayload
  'simulation.running': WsSimulationEventPayload
  'simulation.paused': WsSimulationEventPayload
  'simulation.cancelled': WsSimulationEventPayload
  'simulation.completed': WsSimulationEventPayload
  'simulation.failed': WsSimulationFailedPayload
  'review.stage_completed': WsReviewStageCompletedPayload
  'review.stage_decided': WsReviewStageDecidedPayload
  'review.pipeline_completed': WsReviewPipelineCompletedPayload
  'interrupt.created': WsInterruptCreatedPayload
  'interrupt.resumed': WsInterruptResumedPayload
  'dissent.published': WsDissentPublishedPayload
  'steering.directive.issued': WsSteeringDirectiveIssuedPayload
  'steering.supersession.proposed': WsSteeringSupersessionProposedPayload
  'steering.tasks.superseded': WsSteeringTasksSupersededPayload
}

// Compile-time exhaustiveness guard: every WsEventType value must be a
// key of WsEventPayloadMap. Removing a member from the map is caught
// because the indexed access ``WsEventPayloadMap[K]`` becomes ``never``.
type _WsEventPayloadMapKeyCheck = {
  readonly [K in WsEventType]: WsEventPayloadMap[K]
}
type _WsEventPayloadMapHasEveryEventType = WsEventType extends keyof WsEventPayloadMap
  ? true
  : never
const _wsEventPayloadMapKeyCheck = null as unknown as _WsEventPayloadMapKeyCheck
const _wsEventPayloadMapKeyExhaustiveness = null as unknown as _WsEventPayloadMapHasEveryEventType
void _wsEventPayloadMapKeyCheck
void _wsEventPayloadMapKeyExhaustiveness

/** Filters for WebSocket channel subscriptions. */
export type WsSubscriptionFilters = Readonly<Record<string, string>>

export type WsEventHandler = (event: WsEvent) => void
