import { isDepartmentName } from '@/api/types/enums'
import { createLogger } from '@/lib/logger'
import type {
  ActivityItem,
  DepartmentHealth,
  OverviewMetrics,
  TrendDataPoint,
} from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import type { WsEvent, WsEventType } from '@/api/types/websocket'
import type { MetricCardProps } from '@/components/ui/metric-card'
import { formatCurrency } from '@/utils/format'

const log = createLogger('dashboard')

export type DashboardMetricCardData = Omit<MetricCardProps, 'className'>

const EVENT_DESCRIPTIONS: Partial<Record<WsEventType, string>> = {
  'task.created': 'created a task',
  'task.updated': 'updated a task',
  'task.status_changed': 'changed task status',
  'task.assigned': 'was assigned a task',
  'agent.hired': 'joined the organization',
  'agent.fired': 'left the organization',
  'agent.status_changed': 'changed status',
  'budget.record_added': 'recorded a cost',
  'budget.alert': 'triggered a budget alert',
  'message.sent': 'sent a message',
  'system.error': 'reported a system error',
  'system.startup': 'system started',
  'system.shutdown': 'system shutting down',
  'approval.submitted': 'submitted an approval request',
  'approval.approved': 'approved a request',
  'approval.rejected': 'rejected a request',
  'approval.expired': 'approval expired',
  'meeting.started': 'started a meeting',
  'meeting.completed': 'completed a meeting',
  'meeting.failed': 'meeting failed',
  'coordination.started': 'started coordination',
  'coordination.phase_completed': 'completed a coordination phase',
  'coordination.completed': 'completed coordination',
  'coordination.failed': 'coordination failed',
}

export function computeMetricCards(
  overview: OverviewMetrics,
  budget: BudgetConfig | null,
): DashboardMetricCardData[] {
  const spendTrend = computeSpendTrend(overview.cost_7d_trend)

  return [
    {
      label: 'TASKS',
      value: overview.total_tasks,
      subText: `${overview.tasks_by_status.completed ?? 0} completed`,
    },
    {
      label: 'ACTIVE AGENTS',
      value: overview.active_agents_count,
      subText: `${overview.idle_agents_count} idle`,
    },
    {
      label: 'SPEND',
      value: formatCurrency(overview.total_cost, overview.currency),
      sparklineData:
        overview.cost_7d_trend.length >= 2
          ? overview.cost_7d_trend.map((p) => p.value)
          : undefined,
      change: spendTrend,
      progress: budget
        ? { current: Math.min(overview.total_cost, budget.total_monthly), total: budget.total_monthly }
        : undefined,
      subText: `${Math.round(overview.budget_used_percent)}% of budget`,
    },
    {
      label: 'IN REVIEW',
      value: overview.tasks_by_status.in_review ?? 0,
    },
  ]
}

export function computeSpendTrend(
  points: readonly TrendDataPoint[],
): { value: number; direction: 'up' | 'down' } | undefined {
  if (points.length < 2) return undefined
  const first = points[0]!.value
  const last = points[points.length - 1]!.value
  if (first === 0) return undefined
  const pct = Math.round(Math.abs(((last - first) / first) * 100))
  if (pct === 0) return undefined
  return { value: pct, direction: last >= first ? 'up' : 'down' }
}

export function computeOrgHealth(departments: readonly DepartmentHealth[]): number | null {
  if (departments.length === 0) return null
  const valid = departments.filter((d) => Number.isFinite(d.utilization_percent))
  if (valid.length < departments.length) {
    log.warn(
      `computeOrgHealth: ${departments.length - valid.length} department(s) had non-finite utilization_percent`,
      departments.filter((d) => !Number.isFinite(d.utilization_percent)).map((d) => d.department_name),
    )
  }
  if (valid.length === 0) return null
  const sum = valid.reduce((acc, d) => acc + d.utilization_percent, 0)
  return Math.round(sum / valid.length)
}

export function describeEvent(eventType: WsEventType): string {
  return EVENT_DESCRIPTIONS[eventType] ?? eventType.replace(/[._]/g, ' ')
}

let wsActivityCounter = 0

type WsEventPayload = NonNullable<WsEvent['payload']>

/**
 * Typed empty payload used when a WsEvent arrives without a payload.
 * Every WsEventPayload field is optional, so an empty object is a
 * valid value; binding it to a named constant of the precise type
 * avoids the looser `as WsEventPayload` cast on the read site.
 */
const EMPTY_WS_EVENT_PAYLOAD: WsEventPayload = {}

function _isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value !== ''
}

function _resolveAgentName(payload: WsEventPayload): string {
  if (_isNonEmptyString(payload.agent_name)) return payload.agent_name
  if (_isNonEmptyString(payload.assigned_to)) return payload.assigned_to
  return 'System'
}

function _resolveTaskId(payload: WsEventPayload): string | null {
  return _isNonEmptyString(payload.task_id) ? payload.task_id : null
}

function _resolveDepartment(payload: WsEventPayload): ActivityItem['department'] {
  if (
    typeof payload.department === 'string'
    && isDepartmentName(payload.department)
  ) {
    return payload.department
  }
  return null
}

function _resolveDescription(
  payload: WsEventPayload,
  eventType: WsEventType,
): string {
  if (_isNonEmptyString(payload.description)) return payload.description
  return describeEvent(eventType)
}

interface ActivityIdArgs {
  readonly event: WsEvent
  readonly payload: WsEventPayload
  readonly taskId: string | null
  readonly agentName: string
}

function _resolveActivityId({ event, payload, taskId, agentName }: ActivityIdArgs): string {
  if (_isNonEmptyString(payload.id)) return payload.id
  if (taskId !== null) return taskId
  wsActivityCounter += 1
  return `${event.timestamp}-${event.event_type}-${agentName}-${wsActivityCounter}`
}

export function wsEventToActivityItem(event: WsEvent): ActivityItem {
  const payload: WsEventPayload = event.payload ?? EMPTY_WS_EVENT_PAYLOAD
  const agentName = _resolveAgentName(payload)
  const taskId = _resolveTaskId(payload)
  return {
    id: _resolveActivityId({ event, payload, taskId, agentName }),
    timestamp: event.timestamp,
    agent_name: agentName,
    action_type: event.event_type,
    description: _resolveDescription(payload, event.event_type),
    task_id: taskId,
    department: _resolveDepartment(payload),
  }
}
