/**
 * Budget page utility functions -- pure computations. The sole side effect is
 * a deduped diagnostic warning emitted by {@link computeCategoryBreakdown} when
 * a cost record carries an unrecognised `call_category` (backend enum drift),
 * so the silent fall-through to the uncategorised bucket is observable.
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { formatDateOnly } from '@/utils/format'
import type {
  ActivityItem,
  TrendDataPoint,
} from '@/api/types/analytics'
import type {
  BudgetAlertConfig,
  CostRecord,
  SpendMeasurability,
} from '@/api/types/budget'

// ── Types ──────────────────────────────────────────────────

export const AGGREGATION_PERIOD_VALUES = ['hourly', 'daily', 'weekly'] as const satisfies readonly string[]
export type AggregationPeriod = typeof AGGREGATION_PERIOD_VALUES[number]

export const BREAKDOWN_DIMENSION_VALUES = ['agent', 'department', 'provider', 'prompt_class'] as const satisfies readonly string[]
export type BreakdownDimension = typeof BREAKDOWN_DIMENSION_VALUES[number]

/** Bucket key for cost records carrying no prompt purpose. */
const UNATTRIBUTED_PROMPT_CLASS = 'unattributed'

/**
 * Bucket key for subsystem work owned by no agent.
 *
 * Bucketed rather than dropped: a breakdown that silently omits rows stops
 * summing to the headline total, which is how subsystem spend went missing
 * in the first place.
 */
const UNATTRIBUTED_AGENT = 'unattributed'

/**
 * What the unattributed bucket is called on screen. The bucket keys above are
 * internal join keys that no name map contains, so a row keyed by one would
 * otherwise render the key itself to the operator.
 */
const UNATTRIBUTED_LABEL = 'Unattributed'

/**
 * Severity ordering: normal < amber < red < critical.
 *
 * `unmeasurable` sits outside that ordering: it is not a low severity but the
 * absence of a verdict. A provider billing by flat subscription records a cost
 * of 0.0 on every call, so the percentage the thresholds compare against is a
 * correct zero that measures nothing, and reporting `normal` from it would say
 * the budget is comfortable on precisely the estate nobody can see.
 */
export type ThresholdZone = 'normal' | 'amber' | 'red' | 'critical' | 'unmeasurable'

export interface AgentSpendingRow {
  readonly agentId: string
  readonly agentName: string
  readonly totalCost: number
  readonly budgetPercent: number
  readonly taskCount: number
  readonly costPerTask: number
}

export interface BreakdownSlice {
  readonly key: string
  readonly label: string
  readonly cost: number
  readonly percent: number
  /** CSS custom property value for the slice's chart color. Assigned from the DONUT_COLORS palette cyclically or overridden for aggregated slices. */
  readonly color: string
}

export interface CategoryBucket {
  readonly cost: number
  readonly percent: number
  readonly count: number
}

export interface CategoryRatio {
  readonly productive: CategoryBucket
  readonly coordination: CategoryBucket
  readonly system: CategoryBucket
  readonly embedding: CategoryBucket
  readonly uncategorized: CategoryBucket
}

// ── Constants ──────────────────────────────────────────────

const log = createLogger('budget-utils')

/** Color for the aggregated "Other" slice (overflow beyond the legend cap). */
export const DONUT_COLOR_OTHER = 'var(--so-text-muted)'

/**
 * Rotating palette for cost-breakdown slices, assigned by modulo index. The
 * reserved "Other" colour is deliberately excluded: including it would paint
 * every sixth normal category with the overflow colour. The aggregated "Other"
 * slice sets DONUT_COLOR_OTHER explicitly where it is created.
 */
const DONUT_COLORS: readonly string[] = [
  'var(--so-accent)',
  'var(--so-success)',
  'var(--so-warning)',
  'var(--so-danger)',
  'var(--so-text-secondary)',
]

/** WsEventType values for budget-related events (record additions and alerts). Used by the CFO Activity Feed section to filter the full activity stream. */
const CFO_EVENT_TYPES = new Set(['budget.record_added', 'budget.alert'])

// ── Functions ──────────────────────────────────────────────

/**
 * Group cost records by agent and compute spending metrics.
 *
 * Returns rows sorted by totalCost descending. Agent display names are
 * looked up from `agentNameMap`, falling back to the raw agent_id.
 */
interface AgentCostGroup {
  cost: number
  tasks: Set<string>
}

function groupCostByAgent(
  records: readonly CostRecord[],
): Map<string, AgentCostGroup> {
  const groups = new Map<string, AgentCostGroup>()
  for (const r of records) {
    const agentId = r.agent_id ?? UNATTRIBUTED_AGENT
    let group = groups.get(agentId)
    if (!group) {
      group = { cost: 0, tasks: new Set() }
      groups.set(agentId, group)
    }
    group.cost += r.cost
    // Only real tasks count towards the per-task average; subsystem work
    // has none, so counting it would divide by a task that never existed.
    if (r.task_id !== null) group.tasks.add(r.task_id)
  }
  return groups
}

export function computeAgentSpending(
  records: readonly CostRecord[],
  budgetTotal: number,
  agentNameMap: ReadonlyMap<string, string>,
): AgentSpendingRow[] {
  if (records.length === 0) return []

  const groups = groupCostByAgent(records)
  const rows: AgentSpendingRow[] = []
  for (const [agentId, group] of groups) {
    const taskCount = group.tasks.size
    rows.push({
      agentId,
      agentName:
        agentId === UNATTRIBUTED_AGENT
          ? UNATTRIBUTED_LABEL
          : (agentNameMap.get(agentId) ?? agentId),
      totalCost: group.cost,
      budgetPercent: budgetTotal > 0 ? (group.cost / budgetTotal) * 100 : 0,
      taskCount,
      costPerTask: taskCount > 0 ? group.cost / taskCount : 0,
    })
  }

  return rows.sort((a, b) => b.totalCost - a.totalCost)
}

interface DimensionResolver {
  key: (record: CostRecord, agentDeptMap: ReadonlyMap<string, string>) => string
  label: (key: string, agentNameMap: ReadonlyMap<string, string>) => string
}

const DIMENSION_RESOLVERS: Record<BreakdownDimension, DimensionResolver> = {
  agent: {
    key: (r) => r.agent_id ?? UNATTRIBUTED_AGENT,
    label: (key, agentNameMap) =>
      key === UNATTRIBUTED_AGENT ? UNATTRIBUTED_LABEL : (agentNameMap.get(key) ?? key),
  },
  provider: {
    key: (r) => r.provider,
    label: (key) => key,
  },
  department: {
    key: (r, agentDeptMap) =>
      r.agent_id === null ? 'Unknown' : (agentDeptMap.get(r.agent_id) ?? 'Unknown'),
    label: (key) => key,
  },
  prompt_class: {
    key: (r) => r.prompt_class_id ?? UNATTRIBUTED_PROMPT_CLASS,
    label: (key) => (key === UNATTRIBUTED_PROMPT_CLASS ? UNATTRIBUTED_LABEL : key),
  },
}

/**
 * Group cost records by the given dimension and compute breakdown slices.
 *
 * For the `'department'` dimension, agent IDs are mapped to departments
 * via `agentDeptMap`. Unmapped agents are grouped under "Unknown".
 */
export function computeCostBreakdown(
  records: readonly CostRecord[],
  dimension: BreakdownDimension,
  agentNameMap: ReadonlyMap<string, string>,
  agentDeptMap: ReadonlyMap<string, string>,
): BreakdownSlice[] {
  if (records.length === 0) return []

  const resolver = DIMENSION_RESOLVERS[dimension]
  const groups = new Map<string, number>()
  let totalCost = 0

  for (const r of records) {
    const key = resolver.key(r, agentDeptMap)
    groups.set(key, (groups.get(key) ?? 0) + r.cost)
    totalCost += r.cost
  }

  // Build slices without colors first, then assign colors after sorting
  // so the highest-cost slice always gets the first palette color.
  const unsorted: Omit<BreakdownSlice, 'color'>[] = []
  for (const [key, cost] of groups) {
    unsorted.push({
      key,
      label: resolver.label(key, agentNameMap),
      cost,
      percent: totalCost > 0 ? (cost / totalCost) * 100 : 0,
    })
  }

  unsorted.sort((a, b) => b.cost - a.cost)

  return unsorted.map((s, i) => ({
    ...s,
    color: DONUT_COLORS[i % DONUT_COLORS.length]!,
  }))
}

/**
 * Compute cost category breakdown from cost records.
 *
 * Buckets records by `call_category` (null treated as uncategorized).
 * `image_generation` folds into the productive bucket, mirroring the backend
 * `build_category_breakdown` (flat per-image spend is productive output, not
 * orchestration overhead). Records with unrecognized call_category values fall
 * through to the uncategorized bucket. Returns cost, count, and percentage for
 * each of the five buckets.
 */
export function computeCategoryBreakdown(
  records: readonly CostRecord[],
): CategoryRatio {
  const buckets = {
    productive: { cost: 0, count: 0 },
    coordination: { cost: 0, count: 0 },
    system: { cost: 0, count: 0 },
    embedding: { cost: 0, count: 0 },
    uncategorized: { cost: 0, count: 0 },
  }
  let totalCost = 0
  // Dedupe drift warnings within the call: a large record set sharing one
  // drifted category must not flood the log with identical lines.
  const warnedUnknown = new Set<string>()

  for (const r of records) {
    const rawCat = r.call_category ?? 'uncategorized'
    // Fold image generation into productive to match the backend.
    const cat = rawCat === 'image_generation' ? 'productive' : rawCat
    // ``call_category`` is a backend enum; a value outside the known set
    // (schema drift) falls through to the uncategorized bucket per this
    // function's documented contract. Index through a string-keyed view so a
    // drifted value is honestly typed as a possible miss.
    const byKey: Record<string, { cost: number; count: number }> = buckets
    const known = byKey[cat]
    if (!known && !warnedUnknown.has(cat)) {
      warnedUnknown.add(cat)
      log.warn(
        'Unrecognised cost call_category; bucketed as uncategorized',
        { callCategory: sanitizeForLog(cat) },
      )
    }
    const bucket = known ?? buckets.uncategorized
    bucket.cost += r.cost
    bucket.count += 1
    totalCost += r.cost
  }

  const pct = (cost: number) => (totalCost > 0 ? (cost / totalCost) * 100 : 0)

  return {
    productive: { ...buckets.productive, percent: pct(buckets.productive.cost) },
    coordination: { ...buckets.coordination, percent: pct(buckets.coordination.cost) },
    system: { ...buckets.system, percent: pct(buckets.system.cost) },
    embedding: { ...buckets.embedding, percent: pct(buckets.embedding.cost) },
    uncategorized: { ...buckets.uncategorized, percent: pct(buckets.uncategorized.cost) },
  }
}

/**
 * How a budget percentage is worded, in one place.
 *
 * A money percentage is only a measure of usage where every provider serving
 * the period bills per token. Against a flat subscription the cost is a
 * correct zero that never rises, so "0% of budget" reads as untouched
 * headroom on an estate spending continuously. Every surface that renders the
 * percentage asks here, so a surface cannot be added that renders it bare.
 *
 * @param usedPercent - The percentage as the API computed it.
 * @param measurability - What that percentage covers.
 * @param suffix - Trailing words, e.g. ` of budget`.
 */
export function formatBudgetPercent(
  usedPercent: number | null | undefined,
  measurability: SpendMeasurability | undefined,
  suffix = '',
): string {
  if (usedPercent == null || measurability === undefined) return `--%${suffix}`
  // Mixed is not unmeasurable: part of the estate does bill per token, so the
  // figure covers something and understates the rest. Collapsing the two tells
  // an operator nothing was measured when some of it was.
  if (measurability === 'mixed') return `partly measurable${suffix}`
  if (measurability !== 'measured') return `not measurable${suffix}`
  return `${Math.round(usedPercent)}%${suffix}`
}

/**
 * Determine which threshold zone the current budget usage falls in.
 *
 * `measurability` decides whether the thresholds can be evaluated at all:
 * anything other than `measured` means the percentage is not a measurement,
 * so no severity is derived from it.
 */
export function getThresholdZone(
  usedPercent: number,
  alerts: BudgetAlertConfig,
  measurability: SpendMeasurability,
): ThresholdZone {
  if (measurability !== 'measured') return 'unmeasurable'
  if (usedPercent >= alerts.hard_stop_at) return 'critical'
  if (usedPercent >= alerts.critical_at) return 'red'
  if (usedPercent >= alerts.warn_at) return 'amber'
  return 'normal'
}

/**
 * Compute a human-readable exhaustion date from days remaining.
 *
 * Returns `null` when `daysUntilExhausted` is `null` (no exhaustion projected).
 */
export function computeExhaustionDate(
  daysUntilExhausted: number | null,
): string | null {
  if (daysUntilExhausted === null) return null
  const date = new Date()
  date.setDate(date.getDate() + daysUntilExhausted)
  return formatDateOnly(date.toISOString())
}

/**
 * Aggregate daily trend data points into ISO-week buckets (Monday-based).
 *
 * Each weekly point uses the Monday timestamp and the sum of daily values
 * in that week. Operates on UTC dates -- the Monday boundary is computed
 * using UTC day-of-week to avoid timezone-dependent bucket shifts.
 */
export function aggregateWeekly(
  dataPoints: readonly TrendDataPoint[],
): TrendDataPoint[] {
  if (dataPoints.length === 0) return []

  const weeks = new Map<string, number>()

  for (const point of dataPoints) {
    const date = new Date(point.timestamp)
    const day = date.getUTCDay()
    // Shift to Monday-based: Sunday (0) becomes 6, Monday (1) becomes 0, etc.
    const shift = day === 0 ? 6 : day - 1
    const monday = new Date(date)
    monday.setUTCDate(monday.getUTCDate() - shift)
    const key = monday.toISOString().slice(0, 10)
    weeks.set(key, (weeks.get(key) ?? 0) + point.value)
  }

  const result: TrendDataPoint[] = []
  for (const [timestamp, value] of weeks) {
    result.push({ timestamp, value })
  }
  return result.sort((a, b) => a.timestamp.localeCompare(b.timestamp))
}

/**
 * Compute days remaining until the next billing cycle reset.
 *
 * Clamps `resetDay` to the actual last day of the target month to
 * handle months with fewer days (e.g. resetDay=31 in February).
 */
export function daysUntilBudgetReset(resetDay: number): number {
  if (!Number.isFinite(resetDay) || resetDay < 1 || resetDay > 31) return 0
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth()
  const today = now.getDate()

  const currentMonthLastDay = new Date(year, month + 1, 0).getDate()
  const effectiveResetDay = Math.min(resetDay, currentMonthLastDay)

  if (today < effectiveResetDay) {
    return effectiveResetDay - today
  }
  // Next reset is in the following month
  const nextMonthLastDay = new Date(year, month + 2, 0).getDate()
  const clampedResetDay = Math.min(resetDay, nextMonthLastDay)
  const nextMonth = new Date(year, month + 1, clampedResetDay)
  const diff = nextMonth.getTime() - now.getTime()
  return Math.ceil(diff / (1000 * 60 * 60 * 24))
}

/**
 * Filter activities to only budget-related CFO events.
 */
export function filterCfoEvents(
  activities: readonly ActivityItem[],
): ActivityItem[] {
  return activities.filter((a) => CFO_EVENT_TYPES.has(a.action_type))
}

// ── Pack-Apply Budget Preview ─────────────────────────────

export interface BudgetPreviewDept {
  readonly name: string
  readonly before: number
  readonly after: number
}

export interface BudgetPreview {
  readonly currentTotal: number
  readonly packTotal: number
  readonly projectedTotal: number
  readonly scaleFactor: number
  readonly departments: readonly BudgetPreviewDept[]
}

/**
 * Client-side budget preview for pack application.
 *
 * Mirrors the backend `compute_rebalance(mode=SCALE_EXISTING)` logic
 * so the preview dialog can show accurate before/after numbers.
 */
export function computeBudgetPreview(
  currentDepts: readonly { name: string; budget_percent?: number }[],
  packDeptBudgets: readonly { name: string; budget_percent: number }[],
): BudgetPreview {
  const PRECISION = 10
  const MAX_BUDGET = 100

  const roundTo = (value: number): number => {
    const factor = 10 ** PRECISION
    return Math.round(value * factor) / factor
  }

  const currentTotal = currentDepts.reduce(
    (sum, d) => sum + (d.budget_percent ?? 0),
    0,
  )
  const packTotal = packDeptBudgets.reduce(
    (sum, d) => sum + d.budget_percent,
    0,
  )
  const combined = currentTotal + packTotal

  if (roundTo(combined) <= MAX_BUDGET) {
    return {
      currentTotal,
      packTotal,
      projectedTotal: roundTo(combined),
      scaleFactor: 1,
      departments: [
        ...currentDepts.map((d) => ({
          name: d.name,
          before: d.budget_percent ?? 0,
          after: d.budget_percent ?? 0,
        })),
        ...packDeptBudgets.map((d) => ({
          name: d.name,
          before: 0,
          after: d.budget_percent,
        })),
      ],
    }
  }

  const targetExisting = MAX_BUDGET - packTotal
  const factor = currentTotal <= 0 ? 0 : Math.max(0, Math.min(1, targetExisting / currentTotal))

  const scaledExisting = currentDepts.map((d) => {
    const before = d.budget_percent ?? 0
    return {
      name: d.name,
      before,
      after: roundTo(before * factor),
    }
  })

  const newDepts = packDeptBudgets.map((d) => ({
    name: d.name,
    before: 0,
    after: d.budget_percent,
  }))

  const finalTotal = [...scaledExisting, ...newDepts].reduce(
    (sum, d) => sum + d.after,
    0,
  )

  return {
    currentTotal,
    packTotal,
    projectedTotal: roundTo(finalTotal),
    scaleFactor: roundTo(factor),
    departments: [...scaledExisting, ...newDepts],
  }
}
