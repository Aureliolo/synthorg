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
import type { AgentStatus } from '@/api/types/enums'
import type { MetricCardProps } from '@/components/ui/metric-card'
import type { AgentRuntimeStatus, SemanticColor } from '@/utils/agent-status'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency, formatLabel } from '@/utils/format'

/**
 * What a surface shows where an agent's name belongs and none resolved.
 *
 * The backend sends a name or null, never the identifier, so the fallback is
 * the client's to choose. It says the agent is unknown rather than printing
 * the key, because a key on an operator surface is unreadable at best and,
 * where it lands in prose, tells the operator a UUID is talking to them.
 */
export const UNKNOWN_AGENT_NAME = 'Unknown agent'

/**
 * Read a name out of a map the backend resolved, keyed by the id it stands for.
 *
 * `Object.hasOwn` rather than a bare lookup plus `??`: a plain object answers
 * `Object.prototype` for the key `__proto__`, and an object is not nullish, so
 * the fallback would not fire and the caller would hand React an object to
 * render. Every resolved-name map reaches a surface this way, so the guard
 * lives here once rather than at each render site.
 */
export function resolvedName(
  names: Readonly<Record<string, string>>,
  key: string | null,
  fallback: string,
): string {
  if (key === null || !Object.hasOwn(names, key)) return fallback
  return names[key] ?? fallback
}

/**
 * What a surface shows where nobody holds the role at all.
 *
 * Kept apart from ``UNKNOWN_AGENT_NAME`` because the two states call for
 * different operator action: an unstaffed project needs a lead, while one led
 * by an agent since removed needs the audit log.
 */
export const UNASSIGNED_LABEL = 'Unassigned'

/**
 * What a surface shows for work the system did for itself.
 *
 * A third state again, and not a missing name: work the org does on its own
 * behalf belongs to no agent, so the row carries no agent reference at all
 * rather than one that failed to resolve.
 */
export const SYSTEM_ACTOR_NAME = 'System'

// ── Filter / Sort types ────────────────────────────────────

export interface AgentFilters {
  search?: string | undefined
  // ``department`` is ``string`` (not ``DepartmentName``) so live-config
  // departments created via the setup wizard are accepted -- the static
  // enum only covers the built-in set.
  department?: string | undefined
  status?: AgentStatus | undefined
}

export type AgentSortKey = 'name' | 'department' | 'status' | 'hiring_date'

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

// ── Config extraction (raw dict accessors) ─────────────────

/** Best-effort model identifier from the agent's raw model config dict. */
export function agentModelId(agent: DashboardAgentConfig): string | undefined {
  const id = agent.model['model_id']
  return typeof id === 'string' && id ? id : undefined
}

/** Human-readable personality label from the named preset, if any. */
export function agentPersonalityLabel(agent: DashboardAgentConfig): string | undefined {
  const preset = agent.personality_preset
  return preset ? formatLabel(preset) : undefined
}

/** Personality trait words from the raw personality config, if present. */
export function agentTraits(agent: DashboardAgentConfig): readonly string[] {
  const raw = agent.personality['traits']
  if (!Array.isArray(raw)) return []
  return raw.filter((t): t is string => typeof t === 'string' && t.length > 0)
}

/**
 * What the agent's assigned model can actually do, as resolved by the
 * backend from the provider's capability metadata.
 *
 * Tool calling carries no label here. The matcher only ever assigns a model
 * it believes can call tools, so a positive label would read the same for
 * every agent that has one. The case where that guarantee goes stale is a
 * runtime failure, reported by ``agentToolCallsFailed``.
 *
 * Unresolvable binding or a plain chat model -> [].
 */
export function agentCapabilities(agent: DashboardAgentConfig): readonly string[] {
  const caps = agent.model_capabilities
  if (!caps) return []
  const labels: string[] = []
  if (caps.supports_reasoning) labels.push('reasoning')
  if (caps.supports_vision) labels.push('vision')
  return labels
}

/**
 * True when the agent's ``(provider, model_id)`` binding matches no configured
 * model: a stale or deleted model, not a healthy one that happens to have no
 * extra capabilities. Those two states look identical in every other accessor,
 * so the card needs this one to tell them apart.
 *
 * Reads the status rather than testing ``model_capabilities === null``: the
 * backend also nulls capabilities when provider configuration cannot be read,
 * and inferring from the null alone would accuse every agent in the org of a
 * broken binding during a settings outage.
 */
export function agentModelBindingUnresolved(agent: DashboardAgentConfig): boolean {
  return agent.model_capability_status === 'unresolved'
}

/**
 * True when provider configuration could not be read, so no agent's model
 * capabilities could be resolved. An org-wide outage, not a fault of this
 * agent's binding, which may well be fine.
 */
export function agentCapabilitiesUnavailable(agent: DashboardAgentConfig): boolean {
  return agent.model_capability_status === 'provider_config_unavailable'
}

/**
 * True when runtime tool calls proved the assigned model cannot make them.
 * Distinct from "never observed", which is not a fault.
 */
export function agentToolCallsFailed(agent: DashboardAgentConfig): boolean {
  return agent.model_capabilities?.tool_calling === 'failed'
}

/**
 * True when the assigned model's capabilities were never measured, so the
 * card can say "unverified" rather than implying the model has none. A model
 * binding that does not resolve at all is a different state; see
 * ``agentModelBindingUnresolved``.
 */
export function agentCapabilitiesUnverified(agent: DashboardAgentConfig): boolean {
  return agent.model_capabilities?.metadata_source === 'unknown'
}

// ── Filtering ──────────────────────────────────────────────

/** Client-side filter agents by search, department, and status. */
export function filterAgents(
  agents: readonly DashboardAgentConfig[],
  filters: AgentFilters,
): DashboardAgentConfig[] {
  let result = [...agents]

  if (filters.department) {
    result = result.filter((a) => a.department === filters.department)
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

const STATUS_RANK: Record<AgentStatus, number> = {
  active: 0, onboarding: 1, on_leave: 2, terminated: 3,
}

/**
 * Pull the comparison key for one agent given a sort field. The ordinal
 * `status` field is resolved through its semantic rank table so the sort
 * respects the documented order rather than alpha.
 */
function _sortValue(agent: DashboardAgentConfig, sortBy: AgentSortKey): string | number {
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
  return CAREER_COLOR_MAP[eventType]
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
