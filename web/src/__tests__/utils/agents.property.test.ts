import * as fc from 'fast-check'
import {
  filterAgents,
  sortAgents,
  toRuntimeStatus,
  computePerformanceCards,
  generateInsights,
  formatCompletionTime,
  formatCostPerTask,
  getCareerEventColor,
} from '@/utils/agents'
import type {
  AgentConfig,
  AgentPerformanceSummary,
  TrendResult,
  WindowMetrics,
} from '@/api/types/agents'
import { CAREER_EVENT_TYPE_VALUES } from '@/api/types/agents'
import {
  AGENT_STATUS_VALUES,
  DEPARTMENT_NAME_VALUES,
  SENIORITY_LEVEL_VALUES,
  type AgentStatus,
  type DepartmentName,
  type SeniorityLevel,
} from '@/api/types/enums'

const DEPARTMENTS = [...DEPARTMENT_NAME_VALUES]
const LEVELS = [...SENIORITY_LEVEL_VALUES]
const STATUSES = [...AGENT_STATUS_VALUES]
const CAREER_TYPES = [...CAREER_EVENT_TYPE_VALUES]

// ── Arbitraries ────────────────────────────────────────────

const arbDepartment: fc.Arbitrary<DepartmentName> = fc.constantFrom(...DEPARTMENTS)
const arbLevel: fc.Arbitrary<SeniorityLevel> = fc.constantFrom(...LEVELS)
const arbStatus: fc.Arbitrary<AgentStatus> = fc.constantFrom(...STATUSES)

const arbAgent: fc.Arbitrary<AgentConfig> = fc.record({
  id: fc.uuid(),
  name: fc.tuple(
    fc.constantFrom('Alice', 'Bob', 'Carol', 'Dave', 'Eve', 'Frank'),
    fc.constantFrom('Smith', 'Jones', 'Xu', 'Park', 'Lee', 'Garcia'),
  ).map(([f, l]) => `${f} ${l}`),
  role: fc.constantFrom('Engineer', 'Designer', 'Analyst', 'Manager', 'SRE'),
  department: arbDepartment,
  level: arbLevel,
  status: arbStatus,
  personality: fc.constant({
    traits: ['analytical'],
    communication_style: 'direct',
    risk_tolerance: 'medium' as const,
    creativity: 'high' as const,
    description: 'test',
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    stress_response: 0.5,
    decision_making: 'analytical' as const,
    collaboration: 'team' as const,
    verbosity: 'balanced' as const,
    conflict_approach: 'collaborate' as const,
  }),
  model: fc.constant({
    provider: 'test-provider',
    model_id: 'test-large-001',
    temperature: 0.7,
    max_tokens: 4096,
    fallback_model: null,
  }),
  memory: fc.constant({ type: 'persistent' as const, retention_days: null }),
  tools: fc.constant({ access_level: 'standard' as const, allowed: ['git'], denied: [] }),
  authority: fc.constant({}),
  autonomy_level: fc.constantFrom('full' as const, 'semi' as const, 'supervised' as const, 'locked' as const, null),
  strategic_output_mode: fc.constant(null),
  personality_preset: fc.constant(null),
  tier: fc.constant(null),
  model_requirement: fc.constant(null),
  hiring_date: fc.integer({ min: 1735689600000, max: 1767225600000 }).map(
    (ms) => new Date(ms).toISOString(),
  ),
}, {
  // ``status`` is left optional (sometimes omitted) so the generator still
  // exercises the no-status path that ``filterAgents``/``sortAgents`` treat
  // as active, without emitting an explicit ``undefined`` (rejected under
  // exactOptionalPropertyTypes).
  requiredKeys: [
    'id', 'name', 'role', 'department', 'level', 'personality', 'model',
    'memory', 'tools', 'authority', 'autonomy_level', 'strategic_output_mode',
    'personality_preset', 'tier', 'model_requirement', 'hiring_date',
  ],
})

const arbWindowMetrics: fc.Arbitrary<WindowMetrics> = fc.nat({ max: 100 }).chain((dataPointCount) =>
  fc.nat({ max: dataPointCount }).map((tasksCompleted) => ({
    window_size: '7d' as const,
    data_point_count: dataPointCount,
    tasks_completed: tasksCompleted,
    tasks_failed: dataPointCount - tasksCompleted,
    avg_quality_score: null,
    avg_cost_per_task: null,
    avg_completion_time_seconds: null,
    avg_tokens_per_task: null,
    success_rate: null,
    collaboration_score: null,
    currency: null,
  })),
)

const arbTrendResult: fc.Arbitrary<TrendResult> = fc.record({
  metric_name: fc.constantFrom('success_rate', 'cost_per_task', 'quality_score'),
  window_size: fc.constantFrom('7d', '30d'),
  direction: fc.constantFrom('improving' as const, 'stable' as const, 'declining' as const, 'insufficient_data' as const),
  slope: fc.float({ min: -1, max: 1, noNaN: true }),
  data_point_count: fc.nat({ max: 100 }),
})

const arbPerformance: fc.Arbitrary<AgentPerformanceSummary> = fc.nat({ max: 10000 }).chain((total) => {
  const max30d = Math.min(total, 500)
  return fc.nat({ max: max30d }).chain((completed30d) =>
    fc.record({
      agent_name: fc.string({ minLength: 1, maxLength: 50 }),
      tasks_completed_total: fc.constant(total),
      tasks_completed_7d: fc.nat({ max: Math.min(completed30d, 100) }),
      tasks_completed_30d: fc.constant(completed30d),
    avg_completion_time_seconds: fc.option(fc.float({ min: 0, max: 86400, noNaN: true }), { nil: null }),
    success_rate_percent: fc.option(fc.float({ min: 0, max: 100, noNaN: true }), { nil: null }),
    cost_per_task: fc.option(fc.float({ min: 0, max: 100, noNaN: true }), { nil: null }),
    quality_score: fc.option(fc.float({ min: 0, max: 10, noNaN: true }), { nil: null }),
    collaboration_score: fc.option(fc.float({ min: 0, max: 10, noNaN: true }), { nil: null }),
    trend_direction: fc.constantFrom('improving' as const, 'stable' as const, 'declining' as const, 'insufficient_data' as const),
    windows: fc.array(arbWindowMetrics, { minLength: 0, maxLength: 3 }),
    trends: fc.array(arbTrendResult, { minLength: 0, maxLength: 3 }),
  }))
})

// ── Properties ─────────────────────────────────────────────

describe('toRuntimeStatus properties', () => {
  it('always produces a valid runtime status', () => {
    fc.assert(
      fc.property(arbStatus, (status) => {
        const result = toRuntimeStatus(status)
        expect(['active', 'idle', 'error', 'offline']).toContain(result)
      }),
    )
  })
})

describe('filterAgents properties', () => {
  it('never returns more agents than the input', () => {
    fc.assert(
      fc.property(
        fc.array(arbAgent, { minLength: 0, maxLength: 20 }),
        fc.record({
          search: fc.option(fc.string({ maxLength: 20 }), { nil: undefined }),
          department: fc.option(arbDepartment, { nil: undefined }),
          level: fc.option(arbLevel, { nil: undefined }),
          status: fc.option(arbStatus, { nil: undefined }),
        }),
        (agents, filters) => {
          const result = filterAgents(agents, filters)
          expect(result.length).toBeLessThanOrEqual(agents.length)
        },
      ),
    )
  })

  it('filtering is idempotent', () => {
    fc.assert(
      fc.property(
        fc.array(arbAgent, { minLength: 0, maxLength: 10 }),
        fc.record({
          search: fc.option(fc.string({ maxLength: 10 }), { nil: undefined }),
          department: fc.option(arbDepartment, { nil: undefined }),
        }),
        (agents, filters) => {
          const first = filterAgents(agents, filters)
          const second = filterAgents(first, filters)
          expect(second).toEqual(first)
        },
      ),
    )
  })
})

describe('sortAgents properties', () => {
  it('preserves array length', () => {
    fc.assert(
      fc.property(
        fc.array(arbAgent, { minLength: 0, maxLength: 20 }),
        fc.constantFrom('name' as const, 'department' as const, 'level' as const, 'status' as const, 'hiring_date' as const),
        (agents, sortBy) => {
          const result = sortAgents(agents, sortBy)
          expect(result).toHaveLength(agents.length)
        },
      ),
    )
  })

  it('does not mutate original array', () => {
    fc.assert(
      fc.property(
        fc.array(arbAgent, { minLength: 1, maxLength: 10 }),
        fc.constantFrom('name' as const, 'department' as const),
        (agents, sortBy) => {
          const names = agents.map((a) => a.name)
          sortAgents(agents, sortBy)
          expect(agents.map((a) => a.name)).toEqual(names)
        },
      ),
    )
  })
})

describe('computePerformanceCards properties', () => {
  it('always returns exactly 4 cards', () => {
    fc.assert(
      fc.property(arbPerformance, (perf) => {
        const cards = computePerformanceCards(perf)
        expect(cards).toHaveLength(4)
      }),
    )
  })

  it('all cards have a label and value', () => {
    fc.assert(
      fc.property(arbPerformance, (perf) => {
        const cards = computePerformanceCards(perf)
        for (const card of cards) {
          expect(card.label).toBeTruthy()
          expect(card.value).toBeDefined()
        }
      }),
    )
  })
})

describe('generateInsights properties', () => {
  it('returns at most 3 insights', () => {
    fc.assert(
      fc.property(arbAgent, arbPerformance, (agent, perf) => {
        const insights = generateInsights(agent, perf)
        expect(insights.length).toBeLessThanOrEqual(3)
      }),
    )
  })

  it('returns empty array for null performance', () => {
    fc.assert(
      fc.property(arbAgent, (agent) => {
        const insights = generateInsights(agent, null)
        expect(insights).toHaveLength(0)
      }),
    )
  })

  it('all insights are non-empty strings', () => {
    fc.assert(
      fc.property(arbAgent, arbPerformance, (agent, perf) => {
        const insights = generateInsights(agent, perf)
        for (const insight of insights) {
          expect(typeof insight).toBe('string')
          expect(insight.length).toBeGreaterThan(0)
        }
      }),
    )
  })
})

describe('formatCompletionTime properties', () => {
  it('returns a string for any non-negative number', () => {
    fc.assert(
      fc.property(fc.float({ min: 0, max: 1_000_000, noNaN: true }), (seconds) => {
        const result = formatCompletionTime(seconds)
        expect(typeof result).toBe('string')
        expect(result).not.toBe('--')
      }),
    )
  })
})

describe('formatCostPerTask properties', () => {
  it('returns a currency-formatted string for any non-negative number', () => {
    fc.assert(
      fc.property(fc.float({ min: 0, max: 10000, noNaN: true }), (cost) => {
        const result = formatCostPerTask(cost)
        // Should contain digits and a currency symbol (currency may vary)
        expect(result).toMatch(/\d/)
        expect(result).toMatch(/[^\d\s.,]/)
      }),
    )
  })
})

describe('getCareerEventColor properties', () => {
  it('returns a valid color for all career event types', () => {
    fc.assert(
      fc.property(fc.constantFrom(...CAREER_TYPES), (eventType) => {
        const color = getCareerEventColor(eventType)
        expect(['success', 'accent', 'warning', 'danger']).toContain(color)
      }),
    )
  })
})
