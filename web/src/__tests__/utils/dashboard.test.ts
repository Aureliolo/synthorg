import {
  computeMetricCards,
  computeOrgHealth,
  computeSpendTrend,
  describeEvent,
  wsEventToActivityItem,
} from '@/utils/dashboard'
import type { DepartmentHealth, OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import type { WsEvent } from '@/api/types/websocket'

function makeOverview(overrides: Partial<OverviewMetrics> = {}): OverviewMetrics {
  return {
    total_tasks: 24,
    tasks_by_status: {
      created: 2,
      assigned: 3,
      in_progress: 8,
      in_review: 2,
      completed: 5,
      blocked: 1,
      failed: 1,
      interrupted: 1,
      suspended: 0,
      cancelled: 1,
      rejected: 0,
      auth_required: 0,
    },
    total_agents: 10,
    total_cost: 42.17,
    budget_remaining: 457.83,
    budget_used_percent: 8.43,
    cost_7d_trend: [
      { timestamp: '2026-03-20', value: 5 },
      { timestamp: '2026-03-21', value: 6 },
      { timestamp: '2026-03-22', value: 7 },
      { timestamp: '2026-03-23', value: 5 },
      { timestamp: '2026-03-24', value: 8 },
      { timestamp: '2026-03-25', value: 6 },
      { timestamp: '2026-03-26', value: 5.17 },
    ],
    active_agents_count: 5,
    idle_agents_count: 4,
    currency: 'EUR',
    ...overrides,
  }
}

function makeBudgetConfig(overrides: Partial<BudgetConfig> = {}): BudgetConfig {
  return {
    total_monthly: 500,
    alerts: { warn_at: 80, critical_at: 95, hard_stop_at: 100 },
    per_task_limit: 10,
    per_agent_daily_limit: 20,
    auto_downgrade: {
      enabled: false,
      threshold: 90,
      downgrade_map: [],
      boundary: 'task_assignment',
    },
    reset_day: 1,
    currency: 'EUR',
    pte_tracking_enabled: false,
    ...overrides,
  }
}

describe('computeMetricCards', () => {
  it('returns exactly 4 cards', () => {
    const cards = computeMetricCards(makeOverview(), makeBudgetConfig())
    expect(cards).toHaveLength(4)
  })

  it('includes Tasks card with total_tasks value', () => {
    const cards = computeMetricCards(makeOverview({ total_tasks: 42 }), makeBudgetConfig())
    const tasksCard = cards.find((c) => c.label === 'TASKS')
    expect(tasksCard).toBeDefined()
    expect(tasksCard!.value).toBe(42)
  })

  it('includes Active Agents card', () => {
    const cards = computeMetricCards(
      makeOverview({ active_agents_count: 7, idle_agents_count: 3 }),
      makeBudgetConfig(),
    )
    const agentsCard = cards.find((c) => c.label === 'ACTIVE AGENTS')
    expect(agentsCard).toBeDefined()
    expect(agentsCard!.value).toBe(7)
    expect(agentsCard!.subText).toContain('3')
  })

  it('includes Spend card with formatted currency (EUR default)', () => {
    const cards = computeMetricCards(makeOverview({ total_cost: 42.17 }), makeBudgetConfig())
    const spendCard = cards.find((c) => c.label === 'SPEND')
    expect(spendCard).toBeDefined()
    expect(spendCard!.value).toContain('42.17')
  })

  it('formats Spend card value in overview currency', () => {
    const cards = computeMetricCards(
      makeOverview({ total_cost: 100, currency: 'GBP' }),
      makeBudgetConfig(),
    )
    const spendCard = cards.find((c) => c.label === 'SPEND')
    expect(spendCard!.value).toContain('100.00')
  })

  it('includes sparkline data for spend card from cost_7d_trend', () => {
    const cards = computeMetricCards(makeOverview(), makeBudgetConfig())
    const spendCard = cards.find((c) => c.label === 'SPEND')
    expect(spendCard!.sparklineData).toEqual([5, 6, 7, 5, 8, 6, 5.17])
  })

  it('includes In Review card from tasks_by_status', () => {
    const overview = makeOverview()
    const cards = computeMetricCards(overview, makeBudgetConfig())
    const reviewCard = cards.find((c) => c.label === 'IN REVIEW')
    expect(reviewCard).toBeDefined()
    expect(reviewCard!.value).toBe(2)
  })

  it('includes budget progress on spend card', () => {
    const cards = computeMetricCards(
      makeOverview({ total_cost: 200, budget_used_percent: 40 }),
      makeBudgetConfig({ total_monthly: 500 }),
    )
    const spendCard = cards.find((c) => c.label === 'SPEND')
    expect(spendCard!.progress).toEqual({ current: 200, total: 500 })
  })

  it('clamps spend progress when cost exceeds budget', () => {
    const cards = computeMetricCards(
      makeOverview({ total_cost: 600 }),
      makeBudgetConfig({ total_monthly: 500 }),
    )
    const spendCard = cards.find((c) => c.label === 'SPEND')
    expect(spendCard!.progress!.current).toBe(500)
    expect(spendCard!.progress!.total).toBe(500)
  })

  it('omits sparkline when fewer than 2 trend points', () => {
    const cards = computeMetricCards(
      makeOverview({ cost_7d_trend: [{ timestamp: '2026-03-26', value: 5 }] }),
      makeBudgetConfig(),
    )
    const spendCard = cards.find((c) => c.label === 'SPEND')
    expect(spendCard!.sparklineData).toBeUndefined()
  })
})

describe('computeSpendTrend', () => {
  it('returns undefined for empty data', () => {
    expect(computeSpendTrend([])).toBeUndefined()
  })

  it('returns undefined for single point', () => {
    expect(computeSpendTrend([{ timestamp: '2026-03-20', value: 5 }])).toBeUndefined()
  })

  it('returns up direction when last > first', () => {
    const result = computeSpendTrend([
      { timestamp: '2026-03-20', value: 5 },
      { timestamp: '2026-03-21', value: 10 },
    ])
    expect(result).toEqual({ value: 100, direction: 'up' })
  })

  it('returns down direction when last < first', () => {
    const result = computeSpendTrend([
      { timestamp: '2026-03-20', value: 10 },
      { timestamp: '2026-03-21', value: 5 },
    ])
    expect(result).toEqual({ value: 50, direction: 'down' })
  })

  it('returns undefined when first value is 0', () => {
    const result = computeSpendTrend([
      { timestamp: '2026-03-20', value: 0 },
      { timestamp: '2026-03-21', value: 5 },
    ])
    expect(result).toBeUndefined()
  })

  it('returns undefined when values are equal (0% change)', () => {
    const result = computeSpendTrend([
      { timestamp: '2026-03-20', value: 5 },
      { timestamp: '2026-03-21', value: 5 },
    ])
    expect(result).toBeUndefined()
  })
})

function dh(
  name: DepartmentHealth['department_name'],
  utilization: number,
  agents = 1,
): DepartmentHealth {
  return {
    department_name: name,
    agent_count: agents,
    active_agent_count: agents,
    currency: 'EUR',
    avg_performance_score: null,
    department_cost_7d: 0,
    cost_trend: [],
    collaboration_score: null,
    utilization_percent: utilization,
  }
}

describe('computeOrgHealth', () => {
  it('returns null for empty array', () => {
    expect(computeOrgHealth([])).toBeNull()
  })

  it('returns exact value for single department', () => {
    expect(computeOrgHealth([dh('engineering', 85, 4)])).toBe(85)
  })

  it('averages multiple departments', () => {
    expect(computeOrgHealth([dh('engineering', 80, 4), dh('design', 60, 2)])).toBe(70)
  })

  it('rounds to nearest integer', () => {
    expect(computeOrgHealth([dh('engineering', 33), dh('design', 33), dh('product', 34)])).toBe(33)
  })

  it('filters out NaN utilization_percent values', () => {
    expect(computeOrgHealth([dh('engineering', 80, 4), dh('design', NaN)])).toBe(80)
  })

  it('filters out Infinity utilization_percent values', () => {
    expect(computeOrgHealth([dh('engineering', 60, 2), dh('product', Infinity)])).toBe(60)
  })

  it('returns null when all departments have non-finite utilization', () => {
    expect(computeOrgHealth([dh('design', NaN), dh('product', Infinity)])).toBeNull()
  })
})

describe('describeEvent', () => {
  it('maps task.created', () => {
    expect(describeEvent('task.created')).toBe('created a task')
  })

  it('maps agent.hired', () => {
    expect(describeEvent('agent.hired')).toBe('joined the organization')
  })

  it('maps budget.alert', () => {
    expect(describeEvent('budget.alert')).toBe('triggered a budget alert')
  })

  it('maps coordination.started', () => {
    expect(describeEvent('coordination.started')).toBe('started coordination')
  })

  it('returns fallback for unmapped event types using regex replace', () => {
    // Force a type assertion to test the fallback path with a value not in EVENT_DESCRIPTIONS
    const result = describeEvent('custom.unknown_event' as never)
    expect(result).toBe('custom unknown event')
  })
})

describe('wsEventToActivityItem', () => {
  it('maps a WS event to an ActivityItem', () => {
    const event: WsEvent = {
      event_type: 'task.created',
      channel: 'tasks',
      timestamp: '2026-03-26T10:00:00Z',
      payload: {
        agent_name: 'agent-cto',
        task_id: 'task-123',
        description: 'Implement auth module',
      },
    }
    const item = wsEventToActivityItem(event)
    expect(item.agent_name).toBe('agent-cto')
    expect(item.action_type).toBe('task.created')
    expect(item.timestamp).toBe('2026-03-26T10:00:00Z')
    expect(item.task_id).toBe('task-123')
    expect(item.id).toBeTruthy()
    expect(item.description).toBeTruthy()
  })

  it('handles missing agent_name in payload', () => {
    const event: WsEvent = {
      event_type: 'system.error',
      channel: 'system',
      timestamp: '2026-03-26T10:00:00Z',
      payload: { message: 'Something failed' },
    }
    const item = wsEventToActivityItem(event)
    expect(item.agent_name).toBe('System')
  })

  it('falls back to assigned_to when agent_name is missing', () => {
    const event: WsEvent = {
      event_type: 'task.assigned',
      channel: 'tasks',
      timestamp: '2026-03-26T10:00:00Z',
      payload: { assigned_to: 'agent-dev' },
    }
    const item = wsEventToActivityItem(event)
    expect(item.agent_name).toBe('agent-dev')
  })

  it('falls back to describeEvent when payload.description is missing', () => {
    const event: WsEvent = {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-03-26T10:00:00Z',
      payload: { agent_name: 'new-agent' },
    }
    const item = wsEventToActivityItem(event)
    expect(item.description).toBe('joined the organization')
  })

  it('handles missing task_id in payload', () => {
    const event: WsEvent = {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-03-26T10:00:00Z',
      payload: { agent_name: 'new-agent' },
    }
    const item = wsEventToActivityItem(event)
    expect(item.task_id).toBeNull()
  })
})
