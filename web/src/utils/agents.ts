/** Pure utility functions for agent data transformations. */

import {
  Activity,
  ArrowDownCircle,
  ArrowUpCircle,
  Briefcase,
  CheckCircle2,
  CircleDollarSign,
  Play,
  Send,
  Inbox,
  UserPlus,
  UserMinus,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import type {
  ActivityEventType,
  AgentPerformanceSummary,
  CareerEventType,
  DashboardAgentConfig,
} from '@/api/types/agents'
import type { AgentStatus, SeniorityLevel } from '@/api/types/enums'
import type { MetricCardProps } from '@/components/ui/metric-card'
import type { AgentRuntimeStatus, SemanticColor } from '@/lib/utils'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency } from '@/utils/format'

// ── Filter / Sort types ────────────────────────────────────

export interface AgentFilters {
  search?: string
  // ``department`` is ``string`` (not ``DepartmentName``) so live-config
  // departments created via the setup wizard are accepted -- the static
  // enum only covers the built-in set.
  department?: string
  level?: SeniorityLevel
  status?: AgentStatus
}

export type AgentSortKey = 'name' | 'department' | 'level' | 'status' | 'hiring_date'

// ── Status mapping ─────────────────────────────────────────

const STATUS_MAP: Record<AgentStatus, AgentRuntimeStatus> = {
  active: 'active',
  onboarding: 'idle',
  on_leave: 'idle',
  terminated: 'offline',
}

/** Map HR lifecycle AgentStatus to UI AgentRuntimeStatus. */
export function toRuntimeStatus(status: AgentStatus): AgentRuntimeStatus {
  return STATUS_MAP[status]
}

// ── Filtering ──────────────────────────────────────────────

/** Client-side filter agents by search, department, level, and status. */
export function filterAgents(
  agents: readonly DashboardAgentConfig[],
  filters: AgentFilters,
): DashboardAgentConfig[] {
  let result = [...agents]

  if (filters.department) {
    result = result.filter((a) => a.department === filters.department)
  }
  if (filters.level) {
    result = result.filter((a) => a.level === filters.level)
  }
  if (filters.status) {
    result = result.filter((a) => (a.status ?? 'active') === filters.status)
  }
  if (filters.search) {
    const q = filters.search.trim().toLowerCase()
    if (q) {
      result = result.filter(
        (a) => a.name.toLowerCase().includes(q) || a.role.toLowerCase().includes(q),
      )
    }
  }

  return result
}

// ── Sorting ────────────────────────────────────────────────

/**
 * Single source of truth for seniority ordering across the dashboard.
 * Higher value = more senior.
 */
const SENIORITY_RANK: Readonly<Record<SeniorityLevel, number>> = {
  junior: 0, mid: 1, senior: 2, lead: 3, principal: 4, director: 5, vp: 6, c_suite: 7,
}

/**
 * Return the numeric seniority rank for a level (or ``-1`` for null),
 * suitable for use as a sort key or head-picking tiebreak.
 */
export function seniorityRank(level: SeniorityLevel | null): number {
  return level === null ? -1 : SENIORITY_RANK[level]
}

// Legacy alias retained for the local sort function below.
const LEVEL_RANK = SENIORITY_RANK
const STATUS_RANK: Record<AgentStatus, number> = {
  active: 0, onboarding: 1, on_leave: 2, terminated: 3,
}

/**
 * Pull the comparison key for one agent given a sort field. Ordinal
 * fields (`level`, `status`) are resolved through their semantic rank
 * tables so the sort respects the documented order rather than alpha.
 */
function _sortValue(agent: DashboardAgentConfig, sortBy: AgentSortKey): string | number {
  if (sortBy === 'level') return LEVEL_RANK[agent.level]
  if (sortBy === 'status') return STATUS_RANK[agent.status ?? 'active']
  return agent[sortBy] ?? ''
}

function _compare(a: string | number, b: string | number, dir: number): number {
  if (a < b) return -1 * dir
  if (a > b) return 1 * dir
  return 0
}

/** Sort agents by a given key. Does not mutate the input. */
export function sortAgents(
  agents: readonly DashboardAgentConfig[],
  sortBy: AgentSortKey,
  direction: 'asc' | 'desc' = 'asc',
): DashboardAgentConfig[] {
  const dir = direction === 'asc' ? 1 : -1
  return [...agents].sort((a, b) =>
    _compare(_sortValue(a, sortBy), _sortValue(b, sortBy), dir),
  )
}

// ── Formatting ─────────────────────────────────────────────

/** Format seconds into a human-readable duration string. */
export function formatCompletionTime(seconds: number | null): string {
  if (seconds == null || seconds < 0) return '--'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  if (hours > 0) return `${hours}h ${mins}m`
  return `${mins}m`
}

/** Format a cost value for display in the configured currency. */
export function formatCostPerTask(cost: number | null): string {
  if (cost == null) return '--'
  return formatCurrency(cost, DEFAULT_CURRENCY)
}

// ── Performance cards ──────────────────────────────────────

type PerformanceCardData = Omit<MetricCardProps, 'className'>

type PerformanceWindow = AgentPerformanceSummary['windows'][number]

/**
 * Project sparkline arrays from a performance summary. Returns
 * `undefined` per series when there are fewer than 2 windows so the
 * downstream MetricCard renders a value-only tile rather than a flat
 * line. Nullable per-window metrics are coerced to 0 / 100 as
 * appropriate so the sparkline area never collapses on a sparse window.
 */
function _buildSparklines(perf: AgentPerformanceSummary): {
  readonly task?: number[]
  readonly time?: number[]
  readonly success?: number[]
  readonly cost?: number[]
} {
  if (perf.windows.length < 2) return {}
  const w = perf.windows
  return {
    task: w.map((x: PerformanceWindow) => x.tasks_completed),
    time: w.map((x: PerformanceWindow) => x.avg_completion_time_seconds ?? 0),
    success: w.map((x: PerformanceWindow) =>
      x.success_rate != null ? x.success_rate * 100 : 0,
    ),
    cost: w.map((x: PerformanceWindow) => x.avg_cost_per_task ?? 0),
  }
}

function _successRateText(perf: AgentPerformanceSummary): string {
  return perf.success_rate_percent != null
    ? `${perf.success_rate_percent.toFixed(1)}%`
    : '--'
}

function _successSubText(perf: AgentPerformanceSummary): string | undefined {
  if (perf.tasks_completed_30d <= 0) return undefined
  return `across ${perf.tasks_completed_30d} tasks (30d)`
}

/** Map an AgentPerformanceSummary to 4 MetricCard props. */
export function computePerformanceCards(
  perf: AgentPerformanceSummary,
): PerformanceCardData[] {
  const sparklines = _buildSparklines(perf)
  return [
    {
      label: 'TASKS COMPLETED',
      value: perf.tasks_completed_total,
      subText: `${perf.tasks_completed_7d} this week`,
      sparklineData: sparklines.task,
    },
    {
      label: 'AVG COMPLETION TIME',
      value: formatCompletionTime(perf.avg_completion_time_seconds ?? null),
      sparklineData: sparklines.time,
    },
    {
      label: 'SUCCESS RATE',
      value: _successRateText(perf),
      subText: _successSubText(perf),
      sparklineData: sparklines.success,
    },
    {
      label: 'COST PER TASK',
      value: formatCostPerTask(perf.cost_per_task ?? null),
      sparklineData: sparklines.cost,
    },
  ]
}

// ── Prose insights ─────────────────────────────────────────

/**
 * Generate 0-3 human-readable insight sentences from performance data.
 * The agent parameter is accepted for future personality-based insights but not yet used.
 */
export function generateInsights(
  _agent: DashboardAgentConfig,
  perf: AgentPerformanceSummary | null,
): string[] {
  if (!perf) return []

  const insights: string[] = []

  // Success rate insight
  if (perf.success_rate_percent != null && perf.tasks_completed_total > 0) {
    insights.push(
      `Success rate of ${perf.success_rate_percent.toFixed(1)}% across ${perf.tasks_completed_total} completed tasks.`,
    )
  }

  // Trend insight
  if (perf.trend_direction === 'improving') {
    insights.push('Performance trending upward over the recent window.')
  } else if (perf.trend_direction === 'declining') {
    insights.push('Performance has been declining; may need attention.')
  }

  // Quality insight
  if (perf.quality_score != null && perf.quality_score >= 8.0) {
    insights.push(`Quality score of ${perf.quality_score.toFixed(1)}/10: consistently high output.`)
  }

  return insights.slice(0, 3)
}

// ── Career event colors ────────────────────────────────────

const CAREER_COLOR_MAP: Record<CareerEventType, SemanticColor> = {
  hired: 'success',
  promoted: 'accent',
  onboarded: 'accent',
  demoted: 'warning',
  fired: 'danger',
  offboarded: 'warning',
  status_changed: 'accent',
}

/** Map a career event type to its semantic color. */
export function getCareerEventColor(eventType: CareerEventType): SemanticColor {
  return CAREER_COLOR_MAP[eventType] ?? 'accent'
}

// ── Activity event icons ───────────────────────────────────

const ACTIVITY_ICON_MAP: Partial<Record<ActivityEventType, LucideIcon>> = {
  hired: UserPlus,
  fired: UserMinus,
  promoted: ArrowUpCircle,
  demoted: ArrowDownCircle,
  onboarded: Briefcase,
  task_completed: CheckCircle2,
  task_started: Play,
  cost_incurred: CircleDollarSign,
  tool_used: Wrench,
  delegation_sent: Send,
  delegation_received: Inbox,
}

const FALLBACK_ICON: LucideIcon = Activity

/** Map an activity event type string to a Lucide icon component. */
export function getActivityEventIcon(eventType: string): LucideIcon {
  return ACTIVITY_ICON_MAP[eventType as ActivityEventType] ?? FALLBACK_ICON
}

// The render-time wrapper component (``ActivityEventIcon``) lives in its own
// file at ``./activity-event-icon`` so the ``react-refresh/only-export-components``
// rule isn't tripped by mixing a component export with the utility exports
// in this module. Import it directly from there at call sites.
