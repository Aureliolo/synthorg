import * as fc from 'fast-check'
import {
  computeMetricCards,
  computeOrgHealth,
  describeEvent,
  wsEventToActivityItem,
} from '@/utils/dashboard'
import type { DepartmentHealth, OverviewMetrics, TrendDataPoint } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import { DEPARTMENT_NAME_VALUES } from '@/api/types/enums'
import { WS_EVENT_TYPE_VALUES, type WsEvent } from '@/api/types/websocket'

const WS_EVENT_TYPES = [...WS_EVENT_TYPE_VALUES]
const DEPT_NAMES = [...DEPARTMENT_NAME_VALUES]

const arbIsoTimestamp = fc.integer({ min: 1735689600000, max: 1767225600000 }).map(
  (ms) => new Date(ms).toISOString(),
)

const arbTrendPoint: fc.Arbitrary<TrendDataPoint> = fc.record({
  timestamp: arbIsoTimestamp,
  value: fc.float({ min: 0, max: 10000, noNaN: true }),
})

const arbOverview: fc.Arbitrary<OverviewMetrics> = fc.record({
  total_tasks: fc.nat({ max: 10000 }),
  tasks_by_status: fc.record({
    created: fc.nat({ max: 100 }),
    assigned: fc.nat({ max: 100 }),
    in_progress: fc.nat({ max: 100 }),
    in_review: fc.nat({ max: 100 }),
    completed: fc.nat({ max: 100 }),
    blocked: fc.nat({ max: 100 }),
    failed: fc.nat({ max: 100 }),
    interrupted: fc.nat({ max: 100 }),
    suspended: fc.nat({ max: 100 }),
    cancelled: fc.nat({ max: 100 }),
    rejected: fc.nat({ max: 100 }),
    auth_required: fc.nat({ max: 100 }),
  }),
  total_agents: fc.nat({ max: 100 }),
  total_cost: fc.float({ min: 0, max: 100000, noNaN: true }),
  budget_remaining: fc.float({ min: 0, max: 100000, noNaN: true }),
  budget_used_percent: fc.float({ min: 0, max: 100, noNaN: true }),
  cost_7d_trend: fc.array(arbTrendPoint, { minLength: 0, maxLength: 14 }),
  active_agents_count: fc.nat({ max: 100 }),
  idle_agents_count: fc.nat({ max: 100 }),
  currency: fc.constant('EUR'),
})

const arbBudgetConfig: fc.Arbitrary<BudgetConfig> = fc.record({
  total_monthly: fc.float({ min: 1, max: 100000, noNaN: true }),
  alerts: fc.record({
    warn_at: fc.nat({ max: 100 }),
    critical_at: fc.nat({ max: 100 }),
    hard_stop_at: fc.nat({ max: 100 }),
  }),
  per_task_limit: fc.float({ min: 0, max: 1000, noNaN: true }),
  per_agent_daily_limit: fc.float({ min: 0, max: 1000, noNaN: true }),
  auto_downgrade: fc.record({
    enabled: fc.boolean(),
    threshold: fc.nat({ max: 100 }),
    downgrade_map: fc.constant([] as [string, string][]),
    boundary: fc.constant('task_assignment' as const),
  }),
  reset_day: fc.integer({ min: 1, max: 28 }),
  currency: fc.constant('EUR'),
  pte_tracking_enabled: fc.boolean(),
  risk_budget: fc.constant({
    alerts: { critical_at: 90, warn_at: 75 },
    enabled: false,
    per_agent_daily_risk_limit: 20,
    per_task_risk_limit: 5,
    total_daily_risk_limit: 100,
  }),
})

describe('computeMetricCards (properties)', () => {
  it('always returns exactly 4 cards', () => {
    fc.assert(
      fc.property(arbOverview, arbBudgetConfig, (overview, budget) => {
        const cards = computeMetricCards(overview, budget)
        expect(cards).toHaveLength(4)
      }),
    )
  })

  it('every card has a non-empty label', () => {
    fc.assert(
      fc.property(arbOverview, arbBudgetConfig, (overview, budget) => {
        const cards = computeMetricCards(overview, budget)
        for (const card of cards) {
          expect(card.label.length).toBeGreaterThan(0)
        }
      }),
    )
  })

  it('progress current never exceeds total', () => {
    fc.assert(
      fc.property(arbOverview, arbBudgetConfig, (overview, budget) => {
        const cards = computeMetricCards(overview, budget)
        for (const card of cards) {
          if (card.progress) {
            expect(card.progress.current).toBeLessThanOrEqual(card.progress.total)
          }
        }
      }),
    )
  })
})

describe('computeOrgHealth (properties)', () => {
  it('returns null for empty array and a value in [0, 100] otherwise', () => {
    const arbDeptHealth: fc.Arbitrary<DepartmentHealth> = fc.record({
      department_name: fc.constantFrom(...DEPT_NAMES),
      agent_count: fc.nat({ max: 50 }),
      active_agent_count: fc.nat({ max: 50 }),
      currency: fc.constant('EUR'),
      avg_performance_score: fc.option(fc.float({ min: 0, max: 10, noNaN: true }), { nil: null }),
      department_cost_7d: fc.float({ min: 0, max: 10000, noNaN: true }),
      cost_trend: fc.constant([] as readonly { timestamp: string; value: number }[]),
      collaboration_score: fc.option(fc.float({ min: 0, max: 10, noNaN: true }), { nil: null }),
      utilization_percent: fc.float({ min: 0, max: 100, noNaN: true }),
    })

    fc.assert(
      fc.property(fc.array(arbDeptHealth, { minLength: 0, maxLength: 9 }), (depts) => {
        const result = computeOrgHealth(depts)
        if (depts.length === 0) {
          expect(result).toBeNull()
        } else {
          expect(result).toBeGreaterThanOrEqual(0)
          expect(result).toBeLessThanOrEqual(100)
        }
      }),
    )
  })
})

describe('describeEvent (properties)', () => {
  it('returns a non-empty string for every known event type', () => {
    fc.assert(
      fc.property(fc.constantFrom(...WS_EVENT_TYPES), (eventType) => {
        const description = describeEvent(eventType)
        expect(description.length).toBeGreaterThan(0)
      }),
    )
  })
})

describe('wsEventToActivityItem (properties)', () => {
  it('always produces a valid ActivityItem', () => {
    const arbWsEvent: fc.Arbitrary<WsEvent> = fc.record({
      event_type: fc.constantFrom(...WS_EVENT_TYPES),
      channel: fc.constantFrom('tasks', 'agents', 'budget', 'messages', 'system', 'approvals', 'meetings') as fc.Arbitrary<WsEvent['channel']>,
      timestamp: arbIsoTimestamp,
      payload: fc.record({
        agent_name: fc.option(fc.string({ minLength: 1, maxLength: 30 }), { nil: undefined }),
        task_id: fc.option(fc.uuid(), { nil: undefined }),
      }) as fc.Arbitrary<Record<string, unknown>>,
    })

    fc.assert(
      fc.property(arbWsEvent, (event) => {
        const item = wsEventToActivityItem(event)
        expect(item.id).toBeTruthy()
        expect(item.agent_name).toBeTruthy()
        expect(item.description).toBeTruthy()
        expect(item.timestamp).toBe(event.timestamp)
        expect(item.action_type).toBe(event.event_type)
      }),
    )
  })
})
