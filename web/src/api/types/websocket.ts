/** WebSocket event types, channels and subscription messages. */

// Synchronised with channel constants in `src/synthorg/api/channels.py`.
// Admin-only channels (#dissent, #webhooks, #ratelimit) are not exposed
// to dashboard subscribers; the user-scoped `user:{id}` channel is
// dynamic and is matched by prefix server-side, not by name here.
export const WS_CHANNELS = [
  'tasks', 'agents', 'budget', 'messages', 'system',
  'approvals', 'meetings', 'artifacts', 'projects',
  'company', 'departments', 'clients', 'requests',
  'simulations', 'reviews', 'events', 'interrupts',
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
