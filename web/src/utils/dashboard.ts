import { isDepartmentName, RUN_OUTCOME_VALUES } from '@/api/types/enums'
import type {
  ActivityItem,
  DepartmentHealth,
  OverviewMetrics,
  TrendDataPoint,
} from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import type { RunOutcome } from '@/api/types/enums'
import type { WsEvent, WsEventType } from '@/api/types/websocket'
import type { MetricCardProps } from '@/components/ui/metric-card'
import { createLogger } from '@/lib/logger'
import { formatBudgetPercent } from '@/utils/budget'
import { formatCurrency } from '@/utils/format'
import { sanitizeWsEnumOrNull, sanitizeWsString } from '@/utils/ws-sanitize'

const log = createLogger('dashboard')

export type DashboardMetricCardData = Omit<MetricCardProps, 'className'>

const EVENT_DESCRIPTIONS: Partial<Record<WsEventType, string>> = {
  'task.created': 'created a task',
  'task.updated': 'updated a task',
  'task.status_changed': 'changed task status',
  'task.assigned': 'was assigned a task',
  'agent.hired': 'joined the organisation',
  'agent.fired': 'left the organisation',
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

function sparkline(points: readonly TrendDataPoint[]): number[] | undefined {
  return points.length >= 2 ? points.map((p) => p.value) : undefined
}

export function computeMetricCards(
  overview: OverviewMetrics,
  budget: BudgetConfig | null,
): DashboardMetricCardData[] {
  const spendTrend = computeSpendTrend(overview.cost_7d_trend)
  const { succeeded, empty, failed } = overview.task_outcomes

  return [
    {
      label: 'TASKS',
      value: overview.total_tasks,
      subText: `${overview.tasks_by_status['completed'] ?? 0} completed`,
      sparklineData: sparkline(overview.tasks_7d_trend),
    },
    {
      label: 'ACTIVE AGENTS',
      value: overview.active_agents_count,
      subText: `${overview.idle_agents_count} idle`,
      sparklineData: sparkline(overview.agents_7d_trend),
    },
    {
      label: 'SPEND',
      value: formatCurrency(overview.total_cost, overview.currency),
      sparklineData: sparkline(overview.cost_7d_trend),
      change: spendTrend,
      // Only a measured total is a fraction of the ceiling; a flat-rate
      // provider's correct zero would draw an empty bar reading as headroom.
      // An unset monthly budget is no denominator at all, so there is no
      // fraction to draw either.
      progress:
        budget && budget.total_monthly > 0 && overview.budget_measurability === 'measured'
          ? { current: Math.min(overview.total_cost, budget.total_monthly), total: budget.total_monthly }
          : undefined,
      subText: formatBudgetPercent(
        overview.budget_used_percent,
        overview.budget_measurability,
        ' of budget',
      ),
    },
    {
      // Outcome breakdown surfaces failed and empty runs distinctly. The
      // empty-run count is flagged so a run that produced nothing is never
      // counted as a completion.
      label: 'FAILED RUNS',
      value: failed,
      subText: `${succeeded} succeeded, ${empty} produced nothing`,
    },
  ]
}

export function computeSpendTrend(
  points: readonly TrendDataPoint[],
): { value: number; direction: 'up' | 'down' } | undefined {
  if (points.length < 2) return undefined
  const firstPoint = points.at(0)
  const lastPoint = points.at(-1)
  if (!firstPoint || !lastPoint) return undefined
  const first = firstPoint.value
  const last = lastPoint.value
  if (first === 0) return undefined
  const pct = Math.round(Math.abs(((last - first) / first) * 100))
  if (pct === 0) return undefined
  return { value: pct, direction: last >= first ? 'up' : 'down' }
}

export function computeOrgHealth(departments: readonly DepartmentHealth[]): number | null {
  if (departments.length === 0) return null
  // Average only departments with a real health signal (health_score derived
  // from task outcomes). Departments below the min-activity gate report
  // health_score === null and are skipped; when every department is no-data
  // the overall is null, which the UI renders as an explicit no-data state
  // rather than a misleading number.
  const scores: number[] = []
  for (const dept of departments) {
    const score = dept.health_score
    if (score === null) continue // below the min-activity gate: expected no-data
    if (!Number.isFinite(score)) {
      // A non-finite score can't come from the API (frozen DTOs reject
      // inf/nan), so one here means upstream data corruption worth surfacing.
      log.warn('department reported a non-finite health_score; excluding it', {
        department: dept.department_name,
        healthScore: score,
      })
      continue
    }
    scores.push(score)
  }
  if (scores.length === 0) return null
  const sum = scores.reduce((acc, score) => acc + score, 0)
  return Math.round(sum / scores.length)
}

export function describeEvent(eventType: WsEventType): string {
  return EVENT_DESCRIPTIONS[eventType] ?? eventType.replace(/[._]/g, ' ')
}

let wsActivityCounter = 0

type WsEventPayload = NonNullable<WsEvent['payload']>

function _isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value !== ''
}

function _resolveAgentName(payload: WsEventPayload): string {
  if (_isNonEmptyString(payload['agent_name'])) return payload['agent_name']
  if (_isNonEmptyString(payload['assigned_to'])) return payload['assigned_to']
  return 'System'
}

function _resolveAgentRole(payload: WsEventPayload): string | null {
  // Untrusted WS field: strip control chars / bidi overrides / overlong values
  // before it reaches activity-feed state.
  return _isNonEmptyString(payload['agent_role'])
    ? (sanitizeWsString(payload['agent_role']) ?? null)
    : null
}

function _resolveTaskId(payload: WsEventPayload): string | null {
  return _isNonEmptyString(payload['task_id']) ? payload['task_id'] : null
}

function _resolveRunOutcome(payload: WsEventPayload): RunOutcome | null {
  // Only a terminal task transition carries a run outcome; skip the sanitizer
  // (and its drift warning) when the field is absent, and reject a malformed
  // present value rather than fabricating a "succeeded".
  const raw = payload['run_outcome']
  if (raw === undefined || raw === null) return null
  return sanitizeWsEnumOrNull<RunOutcome>(raw, RUN_OUTCOME_VALUES, {
    field: 'run_outcome',
  })
}

function _resolveDepartment(payload: WsEventPayload): ActivityItem['department'] {
  if (
    typeof payload['department'] === 'string'
    && isDepartmentName(payload['department'])
  ) {
    return payload['department']
  }
  return null
}

function _resolveDescription(
  payload: WsEventPayload,
  eventType: WsEventType,
): string {
  if (_isNonEmptyString(payload['description'])) return payload['description']
  return describeEvent(eventType)
}

interface ActivityIdArgs {
  readonly event: WsEvent
  readonly payload: WsEventPayload
  readonly taskId: string | null
  readonly agentName: string
}

function _resolveActivityId({ event, payload, taskId, agentName }: ActivityIdArgs): string {
  if (_isNonEmptyString(payload['id'])) return payload['id']
  if (taskId !== null) return taskId
  wsActivityCounter += 1
  return `${event.timestamp}-${event.event_type}-${agentName}-${wsActivityCounter}`
}

export function wsEventToActivityItem(event: WsEvent): ActivityItem {
  const payload: WsEventPayload = event.payload
  const agentName = _resolveAgentName(payload)
  const taskId = _resolveTaskId(payload)
  return {
    id: _resolveActivityId({ event, payload, taskId, agentName }),
    timestamp: event.timestamp,
    agent_name: agentName,
    agent_role: _resolveAgentRole(payload),
    action_type: event.event_type,
    description: _resolveDescription(payload, event.event_type),
    task_id: taskId,
    department: _resolveDepartment(payload),
    run_outcome: _resolveRunOutcome(payload),
  }
}
