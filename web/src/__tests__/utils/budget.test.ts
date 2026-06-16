import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActivityItem, ForecastResponse, OverviewMetrics, TrendDataPoint } from '@/api/types/analytics'
import type { BudgetAlertConfig, BudgetConfig, CostRecord } from '@/api/types/budget'
import {
  aggregateWeekly,
  computeAgentSpending,
  computeBudgetMetricCards,
  computeCategoryBreakdown,
  computeCostBreakdown,
  computeExhaustionDate,
  daysUntilBudgetReset,
  filterCfoEvents,
  getThresholdZone,
} from '@/utils/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

// ── Helpers ────────────────────────────────────────────────

function makeRecord(overrides: Partial<CostRecord> = {}): CostRecord {
  return {
    agent_id: 'agent-1',
    task_id: 'task-1',
    project_id: null,
    provider: 'test-provider',
    model: 'test-model-001',
    input_tokens: 100,
    output_tokens: 50,
    cost: 1.0,
    currency: DEFAULT_CURRENCY,
    timestamp: '2026-03-20T10:00:00Z',
    call_category: 'productive',
    accuracy_effort_ratio: null,
    latency_ms: null,
    cache_hit: null,
    retry_count: null,
    retry_reason: null,
    finish_reason: null,
    success: null,
    claim_id: '',
    ...overrides,
  }
}

function makeActivity(overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: 'act-1',
    timestamp: '2026-03-20T10:00:00Z',
    agent_name: 'TestBot',
    action_type: 'budget.record_added',
    description: 'recorded a cost',
    task_id: null,
    department: null,
    ...overrides,
  }
}

const DEFAULT_ALERTS: BudgetAlertConfig = {
  warn_at: 75,
  critical_at: 90,
  hard_stop_at: 100,
}

// ── computeAgentSpending ───────────────────────────────────

describe('computeAgentSpending', () => {
  it('returns empty array for empty records', () => {
    expect(computeAgentSpending([], 100, new Map())).toEqual([])
  })

  it('groups records by agent_id and sums cost', () => {
    const records = [
      makeRecord({ agent_id: 'a1', cost: 3, task_id: 't1' }),
      makeRecord({ agent_id: 'a1', cost: 2, task_id: 't2' }),
      makeRecord({ agent_id: 'a2', cost: 8, task_id: 't3' }),
    ]
    const rows = computeAgentSpending(records, 100, new Map())
    expect(rows).toHaveLength(2)
    expect(rows[0]!.agentId).toBe('a2')
    expect(rows[0]!.totalCost).toBe(8)
    expect(rows[1]!.agentId).toBe('a1')
    expect(rows[1]!.totalCost).toBe(5)
  })

  it('counts unique tasks per agent', () => {
    const records = [
      makeRecord({ agent_id: 'a1', task_id: 't1', cost: 1 }),
      makeRecord({ agent_id: 'a1', task_id: 't1', cost: 2 }),
      makeRecord({ agent_id: 'a1', task_id: 't2', cost: 3 }),
    ]
    const rows = computeAgentSpending(records, 100, new Map())
    expect(rows[0]!.taskCount).toBe(2)
    expect(rows[0]!.costPerTask).toBe(3) // 6 / 2
  })

  it('computes budgetPercent relative to total', () => {
    const records = [
      makeRecord({ agent_id: 'a1', cost: 25 }),
      makeRecord({ agent_id: 'a2', cost: 75 }),
    ]
    const rows = computeAgentSpending(records, 200, new Map())
    expect(rows[0]!.budgetPercent).toBeCloseTo(37.5)
    expect(rows[1]!.budgetPercent).toBeCloseTo(12.5)
  })

  it('handles zero budgetTotal without division error', () => {
    const records = [makeRecord({ agent_id: 'a1', cost: 10 })]
    const rows = computeAgentSpending(records, 0, new Map())
    expect(rows[0]!.budgetPercent).toBe(0)
  })

  it('uses agentNameMap for display names', () => {
    const records = [makeRecord({ agent_id: 'a1' })]
    const nameMap = new Map([['a1', 'Alice Bot']])
    const rows = computeAgentSpending(records, 100, nameMap)
    expect(rows[0]!.agentName).toBe('Alice Bot')
  })

  it('falls back to agent_id when name not in map', () => {
    const records = [makeRecord({ agent_id: 'a1' })]
    const rows = computeAgentSpending(records, 100, new Map())
    expect(rows[0]!.agentName).toBe('a1')
  })

  it('sorts by totalCost descending', () => {
    const records = [
      makeRecord({ agent_id: 'cheap', cost: 1 }),
      makeRecord({ agent_id: 'expensive', cost: 10 }),
      makeRecord({ agent_id: 'mid', cost: 5 }),
    ]
    const rows = computeAgentSpending(records, 100, new Map())
    expect(rows.map((r) => r.agentId)).toEqual(['expensive', 'mid', 'cheap'])
  })
})

// ── computeCostBreakdown ───────────────────────────────────

describe('computeCostBreakdown', () => {
  const emptyMaps = { name: new Map<string, string>(), dept: new Map<string, string>() }

  it('returns empty array for empty records', () => {
    expect(computeCostBreakdown([], 'agent', emptyMaps.name, emptyMaps.dept)).toEqual([])
  })

  it('groups by agent_id', () => {
    const records = [
      makeRecord({ agent_id: 'a1', cost: 3 }),
      makeRecord({ agent_id: 'a2', cost: 7 }),
    ]
    const slices = computeCostBreakdown(records, 'agent', emptyMaps.name, emptyMaps.dept)
    expect(slices).toHaveLength(2)
    expect(slices[0]!.key).toBe('a2')
    expect(slices[0]!.cost).toBe(7)
    expect(slices[0]!.percent).toBeCloseTo(70)
  })

  it('groups by provider', () => {
    const records = [
      makeRecord({ provider: 'prov-a', cost: 4 }),
      makeRecord({ provider: 'prov-b', cost: 6 }),
    ]
    const slices = computeCostBreakdown(records, 'provider', emptyMaps.name, emptyMaps.dept)
    expect(slices).toHaveLength(2)
    expect(slices[0]!.key).toBe('prov-b')
  })

  it('groups by department via agentDeptMap', () => {
    const records = [
      makeRecord({ agent_id: 'a1', cost: 5 }),
      makeRecord({ agent_id: 'a2', cost: 5 }),
    ]
    const deptMap = new Map([['a1', 'Engineering'], ['a2', 'Engineering']])
    const slices = computeCostBreakdown(records, 'department', emptyMaps.name, deptMap)
    expect(slices).toHaveLength(1)
    expect(slices[0]!.key).toBe('Engineering')
    expect(slices[0]!.cost).toBe(10)
  })

  it('groups unmapped agents as "Unknown" for department dimension', () => {
    const records = [makeRecord({ agent_id: 'unknown-agent', cost: 5 })]
    const slices = computeCostBreakdown(records, 'department', emptyMaps.name, emptyMaps.dept)
    expect(slices[0]!.key).toBe('Unknown')
  })

  it('uses agentNameMap for agent dimension labels', () => {
    const records = [makeRecord({ agent_id: 'a1', cost: 5 })]
    const nameMap = new Map([['a1', 'Agent Alpha']])
    const slices = computeCostBreakdown(records, 'agent', nameMap, emptyMaps.dept)
    expect(slices[0]!.label).toBe('Agent Alpha')
  })

  it('sorts slices by cost descending', () => {
    const records = [
      makeRecord({ agent_id: 'a1', cost: 1 }),
      makeRecord({ agent_id: 'a2', cost: 10 }),
      makeRecord({ agent_id: 'a3', cost: 5 }),
    ]
    const slices = computeCostBreakdown(records, 'agent', emptyMaps.name, emptyMaps.dept)
    expect(slices.map((s) => s.key)).toEqual(['a2', 'a3', 'a1'])
  })

  it('assigns colors from DONUT_COLORS palette', () => {
    const records = [
      makeRecord({ agent_id: 'a1', cost: 3 }),
      makeRecord({ agent_id: 'a2', cost: 2 }),
    ]
    const slices = computeCostBreakdown(records, 'agent', emptyMaps.name, emptyMaps.dept)
    expect(slices[0]!.color).toContain('var(--so-')
    expect(slices[1]!.color).toContain('var(--so-')
    // Colors should differ
    expect(slices[0]!.color).not.toBe(slices[1]!.color)
  })
})

// ── computeCategoryBreakdown ───────────────────────────────

describe('computeCategoryBreakdown', () => {
  it('returns all zeros for empty records', () => {
    const ratio = computeCategoryBreakdown([])
    expect(ratio.productive.cost).toBe(0)
    expect(ratio.productive.percent).toBe(0)
    expect(ratio.coordination.cost).toBe(0)
    expect(ratio.system.cost).toBe(0)
    expect(ratio.embedding.cost).toBe(0)
    expect(ratio.uncategorized.cost).toBe(0)
  })

  it('buckets records by call_category', () => {
    const records = [
      makeRecord({ call_category: 'productive', cost: 50 }),
      makeRecord({ call_category: 'coordination', cost: 30 }),
      makeRecord({ call_category: 'system', cost: 20 }),
    ]
    const ratio = computeCategoryBreakdown(records)
    expect(ratio.productive.cost).toBe(50)
    expect(ratio.productive.percent).toBeCloseTo(50)
    expect(ratio.coordination.cost).toBe(30)
    expect(ratio.coordination.percent).toBeCloseTo(30)
    expect(ratio.system.cost).toBe(20)
    expect(ratio.system.percent).toBeCloseTo(20)
  })

  it('treats null call_category as uncategorized', () => {
    const records = [
      makeRecord({ call_category: null, cost: 10 }),
      makeRecord({ call_category: 'productive', cost: 10 }),
    ]
    const ratio = computeCategoryBreakdown(records)
    expect(ratio.uncategorized.cost).toBe(10)
    expect(ratio.uncategorized.count).toBe(1)
    expect(ratio.uncategorized.percent).toBeCloseTo(50)
  })

  it('percentages sum to 100 for non-empty records', () => {
    const records = [
      makeRecord({ call_category: 'productive', cost: 33 }),
      makeRecord({ call_category: 'coordination', cost: 33 }),
      makeRecord({ call_category: 'system', cost: 34 }),
    ]
    const ratio = computeCategoryBreakdown(records)
    const sum =
      ratio.productive.percent +
      ratio.coordination.percent +
      ratio.system.percent +
      ratio.embedding.percent +
      ratio.uncategorized.percent
    expect(sum).toBeCloseTo(100)
  })
})

// ── getThresholdZone ───────────────────────────────────────

describe('getThresholdZone', () => {
  it('returns normal below warn_at', () => {
    expect(getThresholdZone(0, DEFAULT_ALERTS)).toBe('normal')
    expect(getThresholdZone(50, DEFAULT_ALERTS)).toBe('normal')
    expect(getThresholdZone(74.9, DEFAULT_ALERTS)).toBe('normal')
  })

  it('returns amber at warn_at threshold', () => {
    expect(getThresholdZone(75, DEFAULT_ALERTS)).toBe('amber')
    expect(getThresholdZone(80, DEFAULT_ALERTS)).toBe('amber')
    expect(getThresholdZone(89.9, DEFAULT_ALERTS)).toBe('amber')
  })

  it('returns red at critical_at threshold', () => {
    expect(getThresholdZone(90, DEFAULT_ALERTS)).toBe('red')
    expect(getThresholdZone(95, DEFAULT_ALERTS)).toBe('red')
    expect(getThresholdZone(99.9, DEFAULT_ALERTS)).toBe('red')
  })

  it('returns critical at hard_stop_at threshold', () => {
    expect(getThresholdZone(100, DEFAULT_ALERTS)).toBe('critical')
    expect(getThresholdZone(150, DEFAULT_ALERTS)).toBe('critical')
  })
})

// ── computeExhaustionDate ──────────────────────────────────

describe('computeExhaustionDate', () => {
  it('returns null for null input', () => {
    expect(computeExhaustionDate(null)).toBeNull()
  })

  it('returns a date string for zero days', () => {
    const result = computeExhaustionDate(0)
    expect(result).toBeTruthy()
    expect(typeof result).toBe('string')
  })

  it('returns a future date for positive days', () => {
    const result = computeExhaustionDate(30)
    expect(result).toBeTruthy()
    // Should contain month abbreviation and year
    expect(result).toMatch(/\w{3}\s+\d{1,2},\s+\d{4}/)
  })
})

// ── aggregateWeekly ────────────────────────────────────────

describe('aggregateWeekly', () => {
  it('returns empty for empty input', () => {
    expect(aggregateWeekly([])).toEqual([])
  })

  it('groups a single point into one weekly bucket', () => {
    const points: TrendDataPoint[] = [{ timestamp: '2026-03-25', value: 10 }]
    const result = aggregateWeekly(points)
    expect(result).toHaveLength(1)
    expect(result[0]!.value).toBe(10)
  })

  it('sums values within the same week', () => {
    // Mon 2026-03-23 through Sun 2026-03-29
    const points: TrendDataPoint[] = [
      { timestamp: '2026-03-23', value: 5 },  // Monday
      { timestamp: '2026-03-24', value: 3 },  // Tuesday
      { timestamp: '2026-03-25', value: 2 },  // Wednesday
    ]
    const result = aggregateWeekly(points)
    expect(result).toHaveLength(1)
    expect(result[0]!.value).toBe(10)
    expect(result[0]!.timestamp).toBe('2026-03-23')
  })

  it('separates different weeks', () => {
    const points: TrendDataPoint[] = [
      { timestamp: '2026-03-22', value: 5 },  // Sunday (week of Mar 16)
      { timestamp: '2026-03-23', value: 3 },  // Monday (week of Mar 23)
    ]
    const result = aggregateWeekly(points)
    expect(result).toHaveLength(2)
  })

  it('returns results sorted chronologically', () => {
    const points: TrendDataPoint[] = [
      { timestamp: '2026-03-30', value: 7 },  // Week 2
      { timestamp: '2026-03-23', value: 3 },  // Week 1
    ]
    const result = aggregateWeekly(points)
    expect(result[0]!.timestamp < result[1]!.timestamp).toBe(true)
  })
})

// ── daysUntilBudgetReset ───────────────────────────────────

describe('daysUntilBudgetReset', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 2, 5, 12, 0, 0))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns a positive number', () => {
    const days = daysUntilBudgetReset(1)
    expect(days).toBeGreaterThan(0)
  })

  it('returns days remaining when reset is later this month', () => {
    expect(daysUntilBudgetReset(28)).toBe(23)
  })

  it('returns 0 for invalid resetDay', () => {
    expect(daysUntilBudgetReset(NaN)).toBe(0)
    expect(daysUntilBudgetReset(0)).toBe(0)
    expect(daysUntilBudgetReset(32)).toBe(0)
  })
})

// ── filterCfoEvents ────────────────────────────────────────

describe('filterCfoEvents', () => {
  it('returns empty for empty input', () => {
    expect(filterCfoEvents([])).toEqual([])
  })

  it('keeps budget.record_added events', () => {
    const activities = [
      makeActivity({ action_type: 'budget.record_added' }),
      makeActivity({ id: 'act-2', action_type: 'task.created' }),
    ]
    const result = filterCfoEvents(activities)
    expect(result).toHaveLength(1)
    expect(result[0]!.action_type).toBe('budget.record_added')
  })

  it('keeps budget.alert events', () => {
    const activities = [
      makeActivity({ action_type: 'budget.alert' }),
    ]
    const result = filterCfoEvents(activities)
    expect(result).toHaveLength(1)
  })

  it('excludes non-budget events', () => {
    const activities = [
      makeActivity({ action_type: 'task.created' }),
      makeActivity({ id: 'act-2', action_type: 'agent.hired' }),
    ]
    expect(filterCfoEvents(activities)).toHaveLength(0)
  })

  it('preserves order', () => {
    const activities = [
      makeActivity({ id: 'first', action_type: 'budget.alert' }),
      makeActivity({ id: 'middle', action_type: 'task.created' }),
      makeActivity({ id: 'last', action_type: 'budget.record_added' }),
    ]
    const result = filterCfoEvents(activities)
    expect(result.map((a) => a.id)).toEqual(['first', 'last'])
  })
})

// ── computeBudgetMetricCards ───────────────────────────────

describe('computeBudgetMetricCards', () => {
  const overview: OverviewMetrics = {
    total_tasks: 10,
    tasks_by_status: {},
    total_agents: 5,
    total_cost: 42,
    budget_remaining: 58,
    budget_used_percent: 42,
    cost_7d_trend: [] as TrendDataPoint[],
    active_agents_count: 3,
    idle_agents_count: 2,
    currency: 'EUR',
  }

  it('returns 4 cards with correct labels', () => {
    const cards = computeBudgetMetricCards(overview, null, null)
    expect(cards).toHaveLength(4)
    expect(cards[0]!.label).toBe('SPEND THIS PERIOD')
    expect(cards[1]!.label).toBe('BUDGET REMAINING')
    expect(cards[2]!.label).toBe('AVG DAILY SPEND')
    expect(cards[3]!.label).toBe('DAYS UNTIL EXHAUSTED')
  })

  it('formats value fields with currency', () => {
    const cards = computeBudgetMetricCards(overview, null, null)
    // SPEND THIS PERIOD should be a formatted currency string
    expect(cards[0]!.value).toContain('42')
    // BUDGET REMAINING should be a formatted currency string
    expect(cards[1]!.value).toContain('58')
  })

  it('includes progress bar when totalMonthly > 0', () => {
    const budgetConfig: BudgetConfig = {
      total_monthly: 100,
      alerts: { warn_at: 75, critical_at: 90, hard_stop_at: 100 },
      per_task_limit: 5,
      per_agent_daily_limit: 20,
      auto_downgrade: { enabled: false, threshold: 85, downgrade_map: [], boundary: 'task_assignment' },
      reset_day: 1,
      currency: 'EUR',
      pte_tracking_enabled: false,
      forecast_required: true,
      forecast_default_ceiling_multiplier: 1.5,
      run_hard_ceiling: 0,
      forecast_static_prior_per_turn_large: 0.1,
      forecast_static_prior_per_turn_medium: 0.03,
      forecast_static_prior_per_turn_small: 0.005,
      forecast_static_prior_per_turn_local_small: 0,
      forecast_shrinkage_prior_weight: 5,
      benchmark_provider: 'stub',
      model_tier_overrides: {},
      risk_budget: {
        alerts: { critical_at: 90, warn_at: 75 },
        enabled: false,
        per_agent_daily_risk_limit: 20,
        per_task_risk_limit: 5,
        total_daily_risk_limit: 100,
      },
      call_analytics: {
        enabled: true,
        orchestration_alerts: { critical: 0.7, info: 0.3, warn: 0.5 },
        retry_alerts: { warn_rate: 0.1 },
      },
      subscriptions: {},
    }
    const cards = computeBudgetMetricCards(overview, budgetConfig, null)
    expect(cards[0]!.progress).toBeDefined()
    expect(cards[0]!.progress!.current).toBe(42)
    expect(cards[0]!.progress!.total).toBe(100)
  })

  it('shows "N/A" value when days_until_exhausted is null', () => {
    const forecast: ForecastResponse = {
      horizon_days: 14,
      projected_total: 80,
      daily_projections: [],
      days_until_exhausted: null,
      confidence: 0.8,
      avg_daily_spend: 3,
      currency: 'EUR',
    }
    const cards = computeBudgetMetricCards(overview, null, forecast)
    expect(cards[3]!.value).toBe('N/A')
  })
})
