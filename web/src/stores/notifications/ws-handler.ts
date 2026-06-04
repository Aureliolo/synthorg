import { createLogger } from '@/lib/logger'
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'
import type { WsEvent } from '@/api/types/websocket'
import type { EnqueueParams, NotificationsGet } from './types'

const log = createLogger('notifications-store')

type WsPayload = Record<string, unknown>
type WsEnqueueRouter = (payload: WsPayload) => EnqueueParams | null

const BUDGET_ALERT_LEVELS = [
  'threshold',
  'exhausted',
  'hard_stop',
] as const
type BudgetAlertLevel = (typeof BUDGET_ALERT_LEVELS)[number]

const TASK_STATUS_VALUES = ['failed', 'blocked', 'unknown'] as const
type TaskStatusValue = (typeof TASK_STATUS_VALUES)[number]

function approvalSubmitted(p: WsPayload): EnqueueParams {
  return {
    category: 'approvals.pending',
    title: 'Approval requested',
    description: sanitizeWsString(p.title),
    href: typeof p.approval_id === 'string' ? `/approvals` : undefined,
    entityId: sanitizeWsString(p.approval_id),
  }
}

function approvalEntity(
  p: WsPayload,
  category: EnqueueParams['category'],
  title: string,
): EnqueueParams {
  return {
    category,
    title,
    entityId: sanitizeWsString(p.approval_id),
  }
}

function budgetAlert(p: WsPayload): EnqueueParams {
  const level = sanitizeWsEnum<BudgetAlertLevel>(
    p.level,
    BUDGET_ALERT_LEVELS,
    'threshold',
    { maxLen: 32, field: 'budget.alert.level' },
  )
  const isExhausted = level === 'exhausted' || level === 'hard_stop'
  return {
    category: isExhausted ? 'budget.exhausted' : 'budget.threshold',
    title: isExhausted ? 'Budget exhausted' : 'Budget threshold crossed',
    description: sanitizeWsString(p.message),
    severity: isExhausted ? 'critical' : 'warning',
  }
}

function systemError(p: WsPayload): EnqueueParams {
  return {
    category: 'system.error',
    title: 'System error',
    description: sanitizeWsString(p.message),
  }
}

function systemShutdown(): EnqueueParams {
  return { category: 'system.shutdown', title: 'System shutting down' }
}

function personalityTrimmed(p: WsPayload): EnqueueParams {
  const agentName = sanitizeWsString(p.agent_name, 64)
  return {
    category: 'agents.personality_trimmed',
    title: 'Personality trimmed',
    description: agentName
      ? `${agentName} personality was trimmed`
      : undefined,
    entityId: sanitizeWsString(p.agent_id),
  }
}

function agentEvent(
  p: WsPayload,
  category: EnqueueParams['category'],
  title: string,
): EnqueueParams {
  return {
    category,
    title,
    description: sanitizeWsString(p.agent_name, 64),
    entityId: sanitizeWsString(p.agent_id),
  }
}

function taskStatusChanged(p: WsPayload): EnqueueParams | null {
  const status = sanitizeWsEnum<TaskStatusValue>(
    p.status,
    TASK_STATUS_VALUES,
    'unknown',
    { maxLen: 32, field: 'task.status_changed.status' },
  )
  if (status === 'unknown') return null
  const taskId = sanitizeWsString(p.task_id)
  const title = sanitizeWsString(p.title)
  switch (status) {
    case 'failed':
      return {
        category: 'tasks.failed',
        title: 'Task failed',
        description: title,
        entityId: taskId,
        href: taskId ? `/tasks` : undefined,
      }
    case 'blocked':
      return {
        category: 'tasks.blocked',
        title: 'Task blocked',
        description: title,
        entityId: taskId,
      }
    default:
      return null
  }
}

const WS_ROUTERS: Readonly<Record<string, WsEnqueueRouter>> = {
  'approval.submitted': approvalSubmitted,
  'approval.expired': (p) =>
    approvalEntity(p, 'approvals.expiring', 'Approval expiring'),
  'approval.approved': (p) =>
    approvalEntity(p, 'approvals.decided', 'Approval approved'),
  'approval.rejected': (p) =>
    approvalEntity(p, 'approvals.decided', 'Approval rejected'),
  'budget.alert': budgetAlert,
  'system.error': systemError,
  'system.shutdown': systemShutdown,
  'personality.trimmed': personalityTrimmed,
  'agent.hired': (p) => agentEvent(p, 'agents.hired', 'Agent hired'),
  'agent.fired': (p) => agentEvent(p, 'agents.fired', 'Agent fired'),
  'task.status_changed': taskStatusChanged,
}

function handleWsEventImpl(get: NotificationsGet, event: WsEvent): void {
  // Narrow ``event.payload`` (typed Record<string, unknown> on the
  // wire but a malformed broker can still send anything) in one
  // step instead of an unsafe ``as`` followed by a manual typeof.
  if (!isObject(event.payload)) {
    log.warn('Notification WS event has invalid payload', {
      eventType: sanitizeForLog(event.event_type),
    })
    return
  }
  const router = WS_ROUTERS[event.event_type]
  if (!router) return
  const params = router(event.payload)
  if (params === null) return
  get().enqueue(params)
}

export function createWsHandler(get: NotificationsGet) {
  return {
    handleWsEvent: (event: WsEvent) => handleWsEventImpl(get, event),
  }
}
