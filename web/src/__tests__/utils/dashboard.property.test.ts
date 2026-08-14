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
  budget_measurability: fc.constantFrom('measured', 'unmeasurable', 'mixed'),
  cost_7d_trend: fc.array(arbTrendPoint, { minLength: 0, maxLength: 14 }),
  tasks_7d_trend: fc.array(arbTrendPoint, { minLength: 0, maxLength: 14 }),
  agents_7d_trend: fc.array(arbTrendPoint, { minLength: 0, maxLength: 14 }),
  review_7d_trend: fc.array(arbTrendPoint, { minLength: 0, maxLength: 14 }),
  active_agents_count: fc.nat({ max: 100 }),
  idle_agents_count: fc.nat({ max: 100 }),
  task_outcomes: fc.record({
    succeeded: fc.nat({ max: 100 }),
    empty: fc.nat({ max: 100 }),
    failed: fc.nat({ max: 100 }),
  }),
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
  forecast_required: fc.constant(true),
  forecast_default_ceiling_multiplier: fc.constant(1.5),
  run_hard_ceiling: fc.constant(0),
  run_hard_token_ceiling: fc.constant(50000000),
  session_token_ceiling: fc.constant(2000000),
  forecast_static_prior_per_turn_expert: fc.constant(0.1),
  forecast_static_prior_per_turn_capable: fc.constant(0.03),
  forecast_static_prior_per_turn_basic: fc.constant(0.005),
  forecast_static_prior_per_turn_local: fc.constant(0),
  forecast_shrinkage_prior_weight: fc.constant(5),
  benchmark_provider: fc.constant('measured' as const),
  model_capability_overrides: fc.constant({}),
  risk_budget: fc.constant({
    alerts: { critical_at: 90, warn_at: 75 },
    enabled: false,
    per_agent_daily_risk_limit: 20,
    per_task_risk_limit: 5,
    total_daily_risk_limit: 100,
  }),
  call_analytics: fc.constant({
    enabled: true,
    orchestration_alerts: { critical: 0.7, info: 0.3, warn: 0.5 },
    retry_alerts: { warn_rate: 0.1 },
    prompt_class_alerts: { cost_warn: null, p95_latency_warn_ms: null, min_seconds_between_alerts: 300 },
  }),
  subscriptions: fc.constant({}),
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
      total_runs: fc.nat({ max: 100 }),
      task_success_rate: fc.option(fc.float({ min: 0, max: 1, noNaN: true }), { nil: null }),
      utilization_percent: fc.float({ min: 0, max: 100, noNaN: true }),
      utilization_degraded: fc.boolean(),
      // Nullable so randomized inputs exercise computeOrgHealth's no-data
      // filtering path (departments below the min-activity gate report a null
      // health_score), not just the all-finite branch.
      health_score: fc.option(fc.float({ min: 0, max: 100, noNaN: true }), { nil: null }),
    })

    fc.assert(
      fc.property(fc.array(arbDeptHealth, { minLength: 0, maxLength: 9 }), (depts) => {
        const result = computeOrgHealth(depts)
        const hasSignal = depts.some(
          (d) => d.health_score !== null && Number.isFinite(d.health_score),
        )
        if (depts.length === 0 || !hasSignal) {
          // No departments, or every department is no-data -> explicit null.
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
      channel: fc.constantFrom('tasks', 'agents', 'budget', 'messages', 'system', 'approvals', 'meetings'),
      timestamp: arbIsoTimestamp,
      payload: fc.record({
        agent_name: fc.option(fc.string({ minLength: 1, maxLength: 30 }), { nil: undefined }),
        task_id: fc.option(fc.uuid(), { nil: undefined }),
      }),
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
