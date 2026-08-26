import { describe, expect, it } from 'vitest'
import { computeBlockers, computeQueue } from '@/utils/org-pulse'
import type { OverviewMetrics } from '@/api/types/analytics'
import type { SubsystemReport } from '@/api/types/subsystems'
import type { Task } from '@/api/types/tasks'

function overview(overrides: Partial<OverviewMetrics> = {}): OverviewMetrics {
  return {
    total_tasks: 16,
    tasks_by_status: { created: 10, assigned: 5, in_progress: 1 },
    total_agents: 12,
    active_agents_count: 1,
    idle_agents_count: 11,
    total_cost: 0,
    currency: 'EUR',
    budget_remaining: 0,
    budget_used_percent: 0,
    budget_measurability: 'unmeasurable',
    task_outcomes: { succeeded: 0, empty: 0, failed: 0 },
    tasks_7d_trend: [],
    cost_7d_trend: [],
    agents_7d_trend: [],
    review_7d_trend: [],
    ...overrides,
  }
}

function subsystem(overrides: Partial<SubsystemReport> = {}): SubsystemReport {
  return { name: 'memory_backend', phase: 'active', waiting_on: [], detail: null, ...overrides }
}

function blockedTask(reason: Task['blocked_reason'], id: string): Task {
  return {
    id,
    title: 'A task',
    description: 'd',
    type: 'development',
    priority: 'medium',
    project: 'p',
    created_by: 'c',
    created_at: '2026-08-17T10:00:00Z',
    status: 'blocked',
    blocked_reason: reason,
    assigned_to: null,
    assigned_to_name: null,
    dependency_titles: {},
    requested_by_user_id: null,
    plan_id: null,
    plan_item_id: null,
    parent_task_id: null,
    task_structure: null,
    coordination_topology: 'auto',
    estimated_complexity: 'medium',
    stakes: 'normal',
    budget_limit: 0,
    deadline: null,
    max_retries: 1,
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    delegation_chain: [],
    hard_ceiling: null,
    hard_token_ceiling: null,
    forecast_id: null,
    source: null,
    middleware_override: null,
    metadata: {},
  }
}

const NOTHING = { overview: null, blockedTasks: [], subsystems: [] }

describe('computeBlockers', () => {
  it('reports nothing for an org with nothing wrong', () => {
    expect(computeBlockers({ ...NOTHING, overview: overview() })).toEqual([])
  })

  it('says so when no run has produced output', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      overview: overview({ task_outcomes: { succeeded: 0, empty: 5, failed: 5 } }),
    })
    expect(blockers).toHaveLength(1)
    expect(blockers[0]!.severity).toBe('critical')
    expect(blockers[0]!.title).toContain('10 of 10 runs produced nothing')
  })

  it('softens to a warning once something has landed', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      overview: overview({ task_outcomes: { succeeded: 8, empty: 1, failed: 1 } }),
    })
    expect(blockers[0]!.severity).toBe('warning')
  })

  it('stays quiet when every run produced output', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      overview: overview({ task_outcomes: { succeeded: 4, empty: 0, failed: 0 } }),
    })
    expect(blockers).toEqual([])
  })

  it('shows a subsystem\'s own reason verbatim', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      subsystems: [
        subsystem({
          name: 'memory_backend',
          phase: 'blocked',
          detail: 'memory.embedder_model is unset',
        }),
      ],
    })
    // The whole point of surfacing GET /subsystems: an operator can act on this
    // sentence and cannot act on "see the wiring log".
    expect(blockers[0]!.detail).toBe('memory.embedder_model is unset')
    expect(blockers[0]!.title).toContain('blocked')
  })

  it('falls back to the capabilities a subsystem is waiting on', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      subsystems: [
        subsystem({ name: 'docs_engine', phase: 'waiting', waiting_on: ['tool_registry'] }),
      ],
    })
    expect(blockers[0]!.detail).toContain('Tool Registry')
  })

  it('ignores a subsystem that is up', () => {
    expect(
      computeBlockers({ ...NOTHING, subsystems: [subsystem({ phase: 'active' })] }),
    ).toEqual([])
  })

  it('ranks a failed subsystem above one merely waiting', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      subsystems: [
        subsystem({ name: 'a', phase: 'waiting' }),
        subsystem({ name: 'b', phase: 'failed' }),
      ],
    })
    expect(blockers.map((b) => b.severity)).toEqual(['critical', 'warning'])
  })

  it('groups parked tasks by the answer each park waits on', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      blockedTasks: [
        blockedTask('reviewer_unstaffed', 't1'),
        blockedTask('reviewer_unstaffed', 't2'),
        blockedTask('no_capable_agent', 't3'),
      ],
    })
    expect(blockers.map((b) => b.title)).toEqual(['2 tasks blocked', '1 task blocked'])
    expect(blockers[0]!.detail).toBe('waiting for a Completion Reviewer to be staffed')
    expect(blockers[1]!.detail).toBe('no agent capable enough to take it on')
  })

  it('does not treat an unnamed park reason as any of the named ones', () => {
    const blockers = computeBlockers({
      ...NOTHING,
      blockedTasks: [blockedTask(null, 't1')],
    })
    expect(blockers[0]!.detail).toContain('without a stated reason')
  })

  it('reports nothing at all before the overview has loaded', () => {
    expect(computeBlockers(NOTHING)).toEqual([])
  })
})

describe('computeQueue', () => {
  it('sums the statuses that are genuinely waiting', () => {
    expect(computeQueue(overview())).toEqual({ queued: 15, idleAgents: 11 })
  })

  it('does not count work in progress as queued', () => {
    const queue = computeQueue(
      overview({ tasks_by_status: { in_progress: 4, completed: 9 } }),
    )
    expect(queue.queued).toBe(0)
  })

  it('answers zero before the overview has loaded', () => {
    expect(computeQueue(null)).toEqual({ queued: 0, idleAgents: 0 })
  })
})
