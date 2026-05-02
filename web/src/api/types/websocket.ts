/** WebSocket event types, channels and subscription messages. */

// Synchronised with channel constants in `src/synthorg/api/channels.py`.
// Admin-only channels (#dissent, #webhooks, #ratelimit) are not exposed
// to dashboard subscribers; the user-scoped `user:{id}` channel is
// dynamic and is matched by prefix server-side, not by name here.
//
// The `scaling` channel is reserved for future hr.scaling.* events; the
// dashboard's scaling page subscribes to it today, but no Python emitter
// publishes to it yet. Keeping the entry on the wire avoids breaking
// `useScalingData.ts` while the backend HR loop is wired in #1611's
// follow-up. When that lands, the matching enum entries will appear in
// `WsEventType` (Python) + `WS_EVENT_TYPE_VALUES` below.
export const WS_CHANNELS = [
  'tasks', 'agents', 'budget', 'messages', 'system',
  'approvals', 'meetings', 'artifacts', 'projects',
  'company', 'departments', 'clients', 'requests',
  'simulations', 'reviews', 'events', 'interrupts',
  'scaling',
] as const

export type WsChannel = typeof WS_CHANNELS[number]

// Synchronised with `WsEventType` in
// `src/synthorg/api/ws_models.py` -- both lists must match value-for-value.
// The `hr.scaling.*` family was historically declared here without a Python
// counterpart; it was removed pending a future scaling-events surface.
export const WS_EVENT_TYPE_VALUES = [
  'task.created', 'task.updated', 'task.status_changed', 'task.assigned',
  'agent.hired', 'agent.fired', 'agent.status_changed',
  'agent.created', 'agent.updated', 'agent.deleted', 'agents.reordered',
  'company.updated',
  'department.created', 'department.updated', 'department.deleted', 'departments.reordered',
  'personality.trimmed',
  'budget.record_added', 'budget.alert',
  'message.sent',
  'system.error', 'system.startup', 'system.shutdown',
  'approval.submitted', 'approval.approved', 'approval.rejected', 'approval.expired',
  'coordination.started', 'coordination.phase_completed', 'coordination.completed', 'coordination.failed',
  'meeting.started', 'meeting.completed', 'meeting.failed',
  'artifact.created', 'artifact.deleted', 'artifact.content_uploaded',
  'project.created', 'project.deleted', 'project.status_changed',
  'memory.fine_tune.progress', 'memory.fine_tune.stage_changed', 'memory.fine_tune.completed', 'memory.fine_tune.failed',
  'client.created', 'client.updated', 'client.deactivated', 'client.deleted',
  'request.submitted', 'request.scoped', 'request.approved', 'request.rejected', 'request.status_changed',
  'simulation.started', 'simulation.running', 'simulation.paused', 'simulation.cancelled', 'simulation.completed', 'simulation.failed',
  'review.stage_completed', 'review.stage_decided', 'review.pipeline_completed',
  'interrupt.created', 'interrupt.resumed',
  'dissent.published',
] as const

export type WsEventType = (typeof WS_EVENT_TYPE_VALUES)[number]

export interface WsEvent {
  /**
   * Wire-protocol version. Absent on legacy events -- treated as 1.
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

// ─────────────────────────────────────────────────────────────────────
// Typed WS payload contract
// ─────────────────────────────────────────────────────────────────────
//
// Discriminated-union mirror of the Python ``WsEventPayload`` family in
// ``src/synthorg/api/ws_payloads/{_lifecycle,_domain}.py``. Each event
// type has a dedicated payload interface; ``WsEventPayloadMap`` keys
// every event_type to its payload shape; ``WsTypedEvent<T>`` ties them
// together for handlers that narrow on ``event_type``.
//
// New handlers and the typed surface referenced in #1711 use the
// typed shape via ``WsEventOf<T>`` / ``WsTypedEvent<T>``. The dispatch
// loop continues to deliver ``WsEvent`` (untyped payload) so per-store
// migrations to the typed view can roll out incrementally.

export interface WsTaskCreatedPayload {
  task_id: string
  title: string
  status: string
  assigned_agent_id?: string | null
  project_id?: string | null
}

export interface WsTaskUpdatedPayload {
  task_id: string
  title?: string | null
  status?: string | null
  assigned_agent_id?: string | null
}

export interface WsTaskStatusChangedPayload {
  task_id: string
  from_status?: string | null
  to_status: string
}

export interface WsTaskAssignedPayload {
  task_id: string
  agent_id: string
}

export interface WsAgentCreatedPayload {
  name: string
  role: string
  department: string
}

export interface WsAgentUpdatedPayload {
  name: string
  department: string
}

export interface WsAgentDeletedPayload {
  name: string
}

export interface WsAgentHiredPayload {
  agent_id: string
  name: string
  role: string
  department: string
}

export interface WsAgentFiredPayload {
  agent_id: string
  name: string
  reason?: string | null
}

export interface WsAgentStatusChangedPayload {
  agent_id: string
  from_status?: string | null
  to_status: string
}

export interface WsAgentsReorderedPayload {
  department?: string | null
  readonly agent_names: readonly string[]
}

export interface WsCompanyUpdatedPayload {
  company_name?: string | null
  autonomy_level?: string | null
  budget_monthly?: number | null
  communication_pattern?: string | null
}

export interface WsDepartmentCreatedPayload {
  name: string
  description?: string | null
  budget_percent?: number | null
}

export interface WsDepartmentUpdatedPayload {
  name: string
  description?: string | null
}

export interface WsDepartmentDeletedPayload {
  name: string
}

export interface WsDepartmentsReorderedPayload {
  readonly department_names: readonly string[]
}

export interface WsPersonalityTrimmedPayload {
  agent_id: string
  agent_name: string
  task_id: string
  trim_tier: 1 | 2 | 3
  before_tokens: number
  after_tokens: number
  max_tokens: number
  budget_met: boolean
}

export interface WsBudgetRecordAddedPayload {
  amount: number
  currency: string
  category?: string | null
  agent_id?: string | null
}

export interface WsBudgetAlertPayload {
  severity: string
  message: string
  threshold?: number | null
  current?: number | null
  currency: string
}

export interface WsMessagePart {
  type: string
  [key: string]: unknown
}

export interface WsMessageSentPayload {
  message_id: string
  sender: string
  to: string
  content: string
  readonly parts: readonly WsMessagePart[]
}

export interface WsSystemErrorPayload {
  message: string
  code?: string | null
}

export interface WsSystemStartupPayload {
  version?: string | null
}

export interface WsSystemShutdownPayload {
  reason?: string | null
}

export interface WsApprovalEventPayload {
  approval_id: string
  status: string
  action_type: string
  risk_level: string
}

export interface WsCoordinationStartedPayload {
  task_id: string
  agent_count: number
}

export interface WsCoordinationPhaseCompletedPayload {
  task_id: string
  phase: string
  success: boolean
  duration_seconds?: number | null
}

export interface WsCoordinationCompletedPayload {
  task_id: string
  topology: string
  is_success: boolean
  total_duration_seconds: number
}

export interface WsCoordinationFailedPayload {
  task_id: string
  phase?: string | null
  topology?: string | null
  is_success?: boolean | null
  total_duration_seconds?: number | null
  error?: string | null
}

export interface WsMeetingStartedPayload {
  meeting_id: string
  meeting_type: string
  project_id?: string | null
  department?: string | null
  readonly participants: readonly string[]
}

export interface WsMeetingCompletedPayload extends WsMeetingStartedPayload {
  duration_seconds?: number | null
  summary?: string | null
}

export interface WsMeetingFailedPayload extends WsMeetingStartedPayload {
  error?: string | null
  reason?: string | null
}

export interface WsArtifactCreatedPayload {
  artifact_id: string
  task_id: string
  created_by: string
  type: string
}

export interface WsArtifactDeletedPayload {
  artifact_id: string
  task_id: string
}

export interface WsArtifactContentUploadedPayload {
  artifact_id: string
  size_bytes: number
  content_type: string
}

export interface WsProjectCreatedPayload {
  project_id: string
  name: string
  status: string
  lead?: string | null
}

export interface WsProjectDeletedPayload {
  project_id: string
  name: string
}

export interface WsProjectStatusChangedPayload {
  project_id: string
  status: string
  previous_status?: string | null
}

export interface WsMemoryFineTuneEventPayload {
  run_id: string
  stage: string
  progress?: number | null
}

export interface WsMemoryFineTuneStageChangedPayload extends WsMemoryFineTuneEventPayload {
  previous_stage?: string | null
}

export interface WsMemoryFineTuneFailedPayload extends WsMemoryFineTuneEventPayload {
  error?: string | null
}

export interface WsClientEventPayload {
  client_id: string
  name: string
  strictness_level: number
}

export interface WsClientDeletedPayload {
  client_id: string
  name?: string | null
}

export interface WsRequestEventPayload {
  request_id: string
  client_id: string
  status: string
}

export interface WsRequestStatusChangedPayload extends WsRequestEventPayload {
  previous_status?: string | null
}

export interface WsReviewStageCompletedPayload {
  task_id: string
  stage_name: string
}

export interface WsReviewStageDecidedPayload {
  task_id: string
  stage_name: string
  verdict: string
  decided_by: string
}

export interface WsReviewPipelineCompletedPayload {
  task_id: string
  final_verdict?: string | null
}

export interface WsSimulationEventPayload {
  simulation_id: string
  status: string
  progress: number
}

export interface WsSimulationFailedPayload extends WsSimulationEventPayload {
  error?: string | null
}

export interface WsInterruptCreatedPayload {
  interrupt_id: string
  task_id: string
  reason?: string | null
}

export interface WsInterruptResumedPayload {
  interrupt_id: string
  task_id: string
}

export interface WsDissentPublishedPayload {
  task_id: string
  agent_id: string
  message: string
}

/**
 * Map every {@link WsEventType} to its payload interface. The four
 * approval lifecycle events (`approval.submitted` / `approved` /
 * `rejected` / `expired`) and three meeting events share base shapes
 * mirrored from the Python ``_ApprovalEventBase`` / ``_MeetingEventBase``
 * helpers; the simulation, request, client, and memory.fine_tune
 * families do the same.
 *
 * Adding a new {@link WsEventType} member without an entry here is a
 * compile-time error via the ``satisfies`` clause -- this is the
 * primary drift guard between the value tuple and the payload union.
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

/**
 * Generic typed-event view: ``WsTypedEvent<'task.created'>`` carries
 * the full {@link WsTaskCreatedPayload}. Use this for handlers that
 * opt into payload narrowing.
 */
export interface WsTypedEvent<T extends WsEventType = WsEventType> {
  version?: number
  event_type: T
  channel: WsChannel
  timestamp: string
  payload: WsEventPayloadMap[T]
}

/** Convenience alias matching the helper used in {@link WsEventPayloadMap}. */
export type WsEventOf<T extends WsEventType> = WsTypedEvent<T>

/** Filters for WebSocket channel subscriptions. */
export type WsSubscriptionFilters = Readonly<Record<string, string>>

export interface WsSubscribeMessage {
  action: 'subscribe'
  readonly channels: readonly WsChannel[]
  filters?: WsSubscriptionFilters
}

export interface WsUnsubscribeMessage {
  action: 'unsubscribe'
  readonly channels: readonly WsChannel[]
}

export interface WsAckMessage {
  action: 'subscribed' | 'unsubscribed'
  readonly channels: readonly WsChannel[]
}

export interface WsErrorMessage {
  error: string
}

export type WsEventHandler = (event: WsEvent) => void
