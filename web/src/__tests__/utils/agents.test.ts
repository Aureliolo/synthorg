import {
  filterAgents,
  sortAgents,
  toRuntimeStatus,
  computePerformanceCards,
  generateInsights,
  formatCompletionTime,
  formatCostPerTask,
  getCareerEventColor,
  getActivityEventIcon,
} from '@/utils/agents'
import type {
  AgentConfig,
  AgentPerformanceSummary,
  CareerEventType,
} from '@/api/types/agents'
import type { AgentStatus } from '@/api/types/enums'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency } from '@/utils/format'

// ── Factories ──────────────────────────────────────────────

function makeAgent(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return {
    id: 'agent-001',
    name: 'Alice Smith',
    role: 'Software Engineer',
    department: 'engineering',
    level: 'senior',
    status: 'active',
    personality: {
      traits: ['analytical'],
      communication_style: 'direct',
      risk_tolerance: 'medium',
      creativity: 'high',
      description: 'test',
      openness: 0.8,
      conscientiousness: 0.7,
      extraversion: 0.5,
      agreeableness: 0.6,
      stress_response: 0.9,
      decision_making: 'analytical',
      collaboration: 'team',
      verbosity: 'balanced',
      conflict_approach: 'collaborate',
    },
    model: {
      provider: 'test-provider',
      model_id: 'test-large-001',
      temperature: 0.7,
      max_tokens: 4096,
      fallback_model: null,
    },
    memory: { type: 'persistent', retention_days: null },
    tools: { access_level: 'standard', allowed: ['file_system', 'git'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-01-15T00:00:00Z',
    ...overrides,
  }
}

function makePerformance(overrides: Partial<AgentPerformanceSummary> = {}): AgentPerformanceSummary {
  return {
    agent_name: 'Alice Smith',
    tasks_completed_total: 127,
    tasks_completed_7d: 12,
    tasks_completed_30d: 45,
    avg_completion_time_seconds: 3600,
    success_rate_percent: 94.0,
    cost_per_task: 0.35,
    quality_score: 8.2,
    collaboration_score: 7.5,
    trend_direction: 'improving',
    windows: [
      {
        window_size: '7d',
        data_point_count: 12,
        tasks_completed: 12,
        tasks_failed: 1,
        avg_quality_score: 8.0,
        avg_cost_per_task: 0.32,
        avg_completion_time_seconds: 3500,
        avg_tokens_per_task: 2400,
        success_rate: 0.92,
        collaboration_score: 7.2,
        currency: DEFAULT_CURRENCY,
      },
      {
        window_size: '30d',
        data_point_count: 45,
        tasks_completed: 45,
        tasks_failed: 3,
        avg_quality_score: 8.2,
        avg_cost_per_task: 0.35,
        avg_completion_time_seconds: 3600,
        avg_tokens_per_task: 2500,
        success_rate: 0.94,
        collaboration_score: 7.5,
        currency: DEFAULT_CURRENCY,
      },
    ],
    trends: [
      {
        metric_name: 'success_rate',
        window_size: '30d',
        direction: 'improving',
        slope: 0.02,
        data_point_count: 30,
      },
    ],
    ...overrides,
  }
}

// ── toRuntimeStatus ────────────────────────────────────────

describe('toRuntimeStatus', () => {
  it('maps active to active', () => {
    expect(toRuntimeStatus('active')).toBe('active')
  })

  it('maps onboarding to idle', () => {
    expect(toRuntimeStatus('onboarding')).toBe('idle')
  })

  it('maps on_leave to idle', () => {
    expect(toRuntimeStatus('on_leave')).toBe('idle')
  })

  it('maps terminated to offline', () => {
    expect(toRuntimeStatus('terminated')).toBe('offline')
  })

  it('covers all AgentStatus values', () => {
    const statuses: AgentStatus[] = ['active', 'onboarding', 'on_leave', 'terminated']
    for (const s of statuses) {
      expect(toRuntimeStatus(s)).toBeDefined()
    }
  })
})

// ── filterAgents ───────────────────────────────────────────

describe('filterAgents', () => {
  const agents = [
    makeAgent({ name: 'Alice Smith', department: 'engineering', level: 'senior', status: 'active', role: 'Backend Engineer' }),
    makeAgent({ name: 'Bob Jones', department: 'design', level: 'mid', status: 'onboarding', role: 'UI Designer' }),
    makeAgent({ name: 'Carol Xu', department: 'engineering', level: 'lead', status: 'active', role: 'Tech Lead' }),
    makeAgent({ name: 'Dave Park', department: 'operations', level: 'junior', status: 'terminated', role: 'SRE' }),
  ]

  it('returns all agents with no filters', () => {
    const result = filterAgents(agents, {})
    expect(result).toHaveLength(4)
  })

  it('filters by department', () => {
    const result = filterAgents(agents, { department: 'engineering' })
    expect(result).toHaveLength(2)
    expect(result.map((a) => a.name)).toEqual(['Alice Smith', 'Carol Xu'])
  })

  it('filters by level', () => {
    const result = filterAgents(agents, { level: 'mid' })
    expect(result).toHaveLength(1)
    expect(result[0]!.name).toBe('Bob Jones')
  })

  it('filters by status', () => {
    const result = filterAgents(agents, { status: 'active' })
    expect(result).toHaveLength(2)
  })

  it('filters by search query (name)', () => {
    const result = filterAgents(agents, { search: 'alice' })
    expect(result).toHaveLength(1)
    expect(result[0]!.name).toBe('Alice Smith')
  })

  it('filters by search query (role)', () => {
    const result = filterAgents(agents, { search: 'designer' })
    expect(result).toHaveLength(1)
    expect(result[0]!.name).toBe('Bob Jones')
  })

  it('combines multiple filters', () => {
    const result = filterAgents(agents, { department: 'engineering', status: 'active' })
    expect(result).toHaveLength(2)
    expect(result.map((a) => a.name)).toEqual(['Alice Smith', 'Carol Xu'])
  })

  it('returns empty array when no matches', () => {
    const result = filterAgents(agents, { department: 'security' })
    expect(result).toHaveLength(0)
  })

  it('handles empty agent list', () => {
    expect(filterAgents([], { department: 'engineering' })).toHaveLength(0)
  })

  it('search is case-insensitive', () => {
    const result = filterAgents(agents, { search: 'ALICE' })
    expect(result).toHaveLength(1)
  })

  it('treats agent with undefined status as active for filtering', () => {
    const { status: omitStatus, ...noStatusBase } = agents[0]!
    void omitStatus
    const withUndefined = [...agents, { ...noStatusBase, name: 'NoStatus' }]
    const result = filterAgents(withUndefined, { status: 'active' })
    expect(result.map((a) => a.name)).toContain('NoStatus')
  })
})

// ── sortAgents ─────────────────────────────────────────────

describe('sortAgents', () => {
  const agents = [
    makeAgent({ name: 'Carol Xu', department: 'engineering', level: 'lead', hiring_date: '2026-01-01T00:00:00Z' }),
    makeAgent({ name: 'Alice Smith', department: 'design', level: 'senior', hiring_date: '2026-03-01T00:00:00Z' }),
    makeAgent({ name: 'Bob Jones', department: 'operations', level: 'junior', hiring_date: '2026-02-01T00:00:00Z' }),
  ]

  it('treats agent with undefined status as active for sorting', () => {
    const { status: omitStatus, ...noStatus } = makeAgent({ name: 'No Status' })
    void omitStatus
    const withUndefined = [
      makeAgent({ name: 'Terminated Agent', status: 'terminated' }),
      noStatus,
      makeAgent({ name: 'Active Agent', status: 'active' }),
    ]
    const result = sortAgents(withUndefined, 'status', 'asc')
    const names = result.map((a) => a.name)
    // undefined status treated as 'active' -- should sort identically to explicit 'active'
    const activeIdx = names.indexOf('Active Agent')
    const noStatusIdx = names.indexOf('No Status')
    const terminatedIdx = names.indexOf('Terminated Agent')
    // Both active and undefined-status sort before terminated
    expect(terminatedIdx).toBeGreaterThan(activeIdx)
    expect(terminatedIdx).toBeGreaterThan(noStatusIdx)
    // Active and undefined-status share the same rank
    expect(Math.abs(activeIdx - noStatusIdx)).toBeLessThanOrEqual(1)
  })

  it('sorts by name ascending', () => {
    const result = sortAgents(agents, 'name', 'asc')
    expect(result.map((a) => a.name)).toEqual(['Alice Smith', 'Bob Jones', 'Carol Xu'])
  })

  it('sorts by name descending', () => {
    const result = sortAgents(agents, 'name', 'desc')
    expect(result.map((a) => a.name)).toEqual(['Carol Xu', 'Bob Jones', 'Alice Smith'])
  })

  it('sorts by department', () => {
    const result = sortAgents(agents, 'department', 'asc')
    expect(result.map((a) => a.department)).toEqual(['design', 'engineering', 'operations'])
  })

  it('sorts by hiring_date ascending (oldest first)', () => {
    const result = sortAgents(agents, 'hiring_date', 'asc')
    expect(result.map((a) => a.name)).toEqual(['Carol Xu', 'Bob Jones', 'Alice Smith'])
  })

  it('sorts by hiring_date descending (newest first)', () => {
    const result = sortAgents(agents, 'hiring_date', 'desc')
    expect(result.map((a) => a.name)).toEqual(['Alice Smith', 'Bob Jones', 'Carol Xu'])
  })

  it('defaults to ascending', () => {
    const result = sortAgents(agents, 'name')
    expect(result.map((a) => a.name)).toEqual(['Alice Smith', 'Bob Jones', 'Carol Xu'])
  })

  it('sorts by level semantically (junior < senior < lead)', () => {
    const result = sortAgents(agents, 'level', 'asc')
    expect(result.map((a) => a.level)).toEqual(['junior', 'senior', 'lead'])
  })

  it('sorts by status', () => {
    const statusAgents = [
      makeAgent({ name: 'A', status: 'terminated' }),
      makeAgent({ name: 'B', status: 'active' }),
      makeAgent({ name: 'C', status: 'onboarding' }),
    ]
    const result = sortAgents(statusAgents, 'status', 'asc')
    expect(result.map((a) => a.status)).toEqual(['active', 'onboarding', 'terminated'])
  })

  it('does not mutate original array', () => {
    const original = [...agents]
    sortAgents(agents, 'name', 'asc')
    expect(agents.map((a) => a.name)).toEqual(original.map((a) => a.name))
  })
})

// ── formatCompletionTime ───────────────────────────────────

describe('formatCompletionTime', () => {
  it('formats seconds under a minute', () => {
    expect(formatCompletionTime(45)).toBe('45s')
  })

  it('formats exactly 60 seconds as 1 minute', () => {
    expect(formatCompletionTime(60)).toBe('1m')
  })

  it('formats minutes', () => {
    expect(formatCompletionTime(120)).toBe('2m')
  })

  it('formats hours and minutes', () => {
    expect(formatCompletionTime(3661)).toBe('1h 1m')
  })

  it('formats exact hours', () => {
    expect(formatCompletionTime(3600)).toBe('1h 0m')
  })

  it('returns -- for null', () => {
    expect(formatCompletionTime(null)).toBe('--')
  })

  it('returns -- for negative', () => {
    expect(formatCompletionTime(-10)).toBe('--')
  })
})

// ── formatCostPerTask ──────────────────────────────────────

describe('formatCostPerTask', () => {
  it('formats cost via the canonical formatCurrency default', () => {
    const result = formatCostPerTask(0.35)
    expect(result).toBe(formatCurrency(0.35, DEFAULT_CURRENCY))
  })

  it('formats larger cost', () => {
    const result = formatCostPerTask(12.5)
    expect(result).toContain('12.50')
  })

  it('returns -- for null', () => {
    expect(formatCostPerTask(null)).toBe('--')
  })

  it('formats negative cost', () => {
    const result = formatCostPerTask(-5)
    expect(result).toContain('5.00')
  })
})

// ── computePerformanceCards ────────────────────────────────

describe('computePerformanceCards', () => {
  it('returns 4 metric cards', () => {
    const cards = computePerformanceCards(makePerformance())
    expect(cards).toHaveLength(4)
  })

  it('includes tasks completed card', () => {
    const cards = computePerformanceCards(makePerformance())
    const tasksCard = cards.find((c) => c.label === 'TASKS COMPLETED')
    expect(tasksCard).toBeDefined()
    expect(tasksCard!.value).toBe(127)
  })

  it('includes success rate card', () => {
    const cards = computePerformanceCards(makePerformance())
    const rateCard = cards.find((c) => c.label === 'SUCCESS RATE')
    expect(rateCard).toBeDefined()
    expect(rateCard!.value).toBe('94.0%')
  })

  it('includes avg completion time card', () => {
    const cards = computePerformanceCards(makePerformance())
    const timeCard = cards.find((c) => c.label === 'AVG COMPLETION TIME')
    expect(timeCard).toBeDefined()
    expect(timeCard!.value).toBe('1h 0m')
  })

  it('includes cost per task card', () => {
    const cards = computePerformanceCards(makePerformance())
    const costCard = cards.find((c) => c.label === 'COST PER TASK')
    expect(costCard).toBeDefined()
    expect(String(costCard!.value)).toContain('0.35')
  })

  it('handles null values gracefully', () => {
    const cards = computePerformanceCards(makePerformance({
      avg_completion_time_seconds: null,
      success_rate_percent: null,
      cost_per_task: null,
    }))
    expect(cards).toHaveLength(4)
    const timeCard = cards.find((c) => c.label === 'AVG COMPLETION TIME')
    expect(timeCard!.value).toBe('--')
  })

  it('includes sparkline data when multiple windows exist', () => {
    const cards = computePerformanceCards(makePerformance())
    const tasksCard = cards.find((c) => c.label === 'TASKS COMPLETED')
    expect(tasksCard!.sparklineData).toBeDefined()
    expect(tasksCard!.sparklineData).toHaveLength(2)
  })

  it('omits sparkline data when fewer than 2 windows', () => {
    const cards = computePerformanceCards(makePerformance({ windows: [] }))
    const tasksCard = cards.find((c) => c.label === 'TASKS COMPLETED')
    expect(tasksCard!.sparklineData).toBeUndefined()
  })
})

// ── generateInsights ───────────────────────────────────────

describe('generateInsights', () => {
  it('generates at least one insight for a performing agent', () => {
    const insights = generateInsights(makeAgent(), makePerformance())
    expect(insights.length).toBeGreaterThan(0)
  })

  it('mentions success rate when high', () => {
    const insights = generateInsights(makeAgent(), makePerformance({ success_rate_percent: 98 }))
    const hasSuccessInsight = insights.some((i) => i.toLowerCase().includes('success'))
    expect(hasSuccessInsight).toBe(true)
  })

  it('mentions trend direction when improving', () => {
    const insights = generateInsights(makeAgent(), makePerformance({ trend_direction: 'improving' }))
    const hasTrend = insights.some((i) => i.toLowerCase().includes('improving') || i.toLowerCase().includes('upward'))
    expect(hasTrend).toBe(true)
  })

  it('mentions declining trend', () => {
    const insights = generateInsights(makeAgent(), makePerformance({ trend_direction: 'declining' }))
    const hasDeclining = insights.some((i) => i.toLowerCase().includes('declining') || i.toLowerCase().includes('attention'))
    expect(hasDeclining).toBe(true)
  })

  it('does not generate quality insight below threshold', () => {
    const insights = generateInsights(makeAgent(), makePerformance({ quality_score: 7.9, trend_direction: 'stable' }))
    const hasQuality = insights.some((i) => i.toLowerCase().includes('quality'))
    expect(hasQuality).toBe(false)
  })

  it('returns empty array when performance is null', () => {
    const insights = generateInsights(makeAgent(), null)
    expect(insights).toHaveLength(0)
  })

  it('handles zero tasks gracefully', () => {
    const insights = generateInsights(
      makeAgent(),
      makePerformance({ tasks_completed_total: 0, success_rate_percent: null }),
    )
    expect(Array.isArray(insights)).toBe(true)
  })

  it('returns at most 3 insights', () => {
    const insights = generateInsights(makeAgent(), makePerformance())
    expect(insights.length).toBeLessThanOrEqual(3)
  })
})

// ── getCareerEventColor ────────────────────────────────────

describe('getCareerEventColor', () => {
  it('returns success for hired', () => {
    expect(getCareerEventColor('hired')).toBe('success')
  })

  it('returns accent for promoted', () => {
    expect(getCareerEventColor('promoted')).toBe('accent')
  })

  it('returns danger for fired', () => {
    expect(getCareerEventColor('fired')).toBe('danger')
  })

  it('returns warning for demoted', () => {
    expect(getCareerEventColor('demoted')).toBe('warning')
  })

  it('returns accent for onboarded', () => {
    expect(getCareerEventColor('onboarded')).toBe('accent')
  })

  it('covers all CareerEventType values', () => {
    const types: CareerEventType[] = ['hired', 'fired', 'promoted', 'demoted', 'onboarded']
    for (const t of types) {
      expect(getCareerEventColor(t)).toBeDefined()
    }
  })
})

// ── getActivityEventIcon ───────────────────────────────────

describe('getActivityEventIcon', () => {
  it('returns an icon for known event types', () => {
    const knownTypes = [
      'hired', 'fired', 'promoted', 'demoted', 'onboarded',
      'task_completed', 'task_started', 'cost_incurred',
      'tool_used', 'delegation_sent', 'delegation_received',
    ]
    for (const t of knownTypes) {
      const icon = getActivityEventIcon(t)
      expect(icon).toBeDefined()
    }
  })

  it('returns a fallback icon for unknown types', () => {
    const icon = getActivityEventIcon('unknown_event')
    expect(icon).toBeDefined()
  })
})
