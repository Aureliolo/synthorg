import { describe, expect, it } from 'vitest'
import fc from 'fast-check'
import type { Priority, TaskStatus, TaskType } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import {
  type TaskBoardFilters,
  KANBAN_COLUMNS,
  filterTasks,
  groupTasksByColumn,
} from '@/utils/tasks'

const ALL_STATUSES: TaskStatus[] = [
  'created', 'assigned', 'in_progress', 'in_review', 'completed',
  'blocked', 'failed', 'interrupted', 'cancelled',
]

const ALL_PRIORITIES: Priority[] = ['critical', 'high', 'medium', 'low']

const ALL_TASK_TYPES: TaskType[] = ['development', 'design', 'research', 'review', 'meeting', 'admin']

const arbTaskStatus = fc.constantFrom(...ALL_STATUSES)
const arbPriority = fc.constantFrom(...ALL_PRIORITIES)
const arbTaskType = fc.constantFrom(...ALL_TASK_TYPES)
const arbAgentName = fc.constantFrom('agent-a', 'agent-b', 'agent-c', null)

function arbTask(): fc.Arbitrary<Task> {
  return fc.record({
    id: fc.uuid(),
    title: fc.string({ minLength: 1, maxLength: 100 }),
    description: fc.string({ maxLength: 200 }),
    type: arbTaskType,
    status: arbTaskStatus,
    priority: arbPriority,
    project: fc.constant('test-project'),
    created_by: fc.constant('agent-cto'),
    assigned_to: arbAgentName,
    reviewers: fc.constant([] as readonly string[]),
    dependencies: fc.constant([] as readonly string[]),
    artifacts_expected: fc.constant([] as readonly { readonly path: string; readonly type: 'code' | 'tests' | 'documentation' }[]),
    acceptance_criteria: fc.constant([] as readonly { readonly description: string; readonly met: boolean }[]),
    estimated_complexity: fc.constantFrom('simple', 'medium', 'complex', 'epic') as fc.Arbitrary<Task['estimated_complexity']>,
    budget_limit: fc.nat({ max: 1000 }),
    deadline: fc.constant(null),
    max_retries: fc.nat({ max: 5 }),
    parent_task_id: fc.constant(null),
    delegation_chain: fc.constant([] as readonly string[]),
    task_structure: fc.constant(null),
    coordination_topology: fc.constant('auto' as const),
  })
}

describe('tasks property tests', () => {
  describe('groupTasksByColumn', () => {
    it('preserves all tasks -- no task is lost or duplicated', () => {
      fc.assert(
        fc.property(fc.array(arbTask(), { maxLength: 50 }), (tasks) => {
          const grouped = groupTasksByColumn(tasks)
          const total = Object.values(grouped).reduce((sum, arr) => sum + arr.length, 0)
          expect(total).toBe(tasks.length)
        }),
        { numRuns: 100 },
      )
    })

    it('every task lands in exactly one column', () => {
      fc.assert(
        fc.property(arbTask(), (task) => {
          const grouped = groupTasksByColumn([task])
          const columnCounts = KANBAN_COLUMNS.map((col) => grouped[col.id].length)
          expect(columnCounts.filter((c) => c === 1)).toHaveLength(1)
          expect(columnCounts.filter((c) => c === 0)).toHaveLength(KANBAN_COLUMNS.length - 1)
        }),
        { numRuns: 100 },
      )
    })
  })

  describe('filterTasks', () => {
    it('never crashes with any combination of filters', () => {
      const arbFilters: fc.Arbitrary<TaskBoardFilters> = fc.record({
        status: fc.option(arbTaskStatus, { nil: undefined }),
        priority: fc.option(arbPriority, { nil: undefined }),
        assignee: fc.option(fc.string(), { nil: undefined }),
        taskType: fc.option(arbTaskType, { nil: undefined }),
        search: fc.option(fc.string(), { nil: undefined }),
      }, { requiredKeys: [] })

      fc.assert(
        fc.property(fc.array(arbTask(), { maxLength: 30 }), arbFilters, (tasks, filters) => {
          const result = filterTasks(tasks, filters)
          expect(Array.isArray(result)).toBe(true)
        }),
        { numRuns: 100 },
      )
    })

    it('filtered result is always a subset of input', () => {
      const arbFilters: fc.Arbitrary<TaskBoardFilters> = fc.record({
        status: fc.option(arbTaskStatus, { nil: undefined }),
        priority: fc.option(arbPriority, { nil: undefined }),
        assignee: fc.option(fc.constantFrom('agent-a', 'agent-b', 'agent-c'), { nil: undefined }),
        search: fc.option(fc.string({ maxLength: 10 }), { nil: undefined }),
      }, { requiredKeys: [] })

      fc.assert(
        fc.property(fc.array(arbTask(), { maxLength: 30 }), arbFilters, (tasks, filters) => {
          const result = filterTasks(tasks, filters)
          expect(result.length).toBeLessThanOrEqual(tasks.length)
          for (const task of result) {
            expect(tasks).toContain(task)
          }
        }),
        { numRuns: 100 },
      )
    })

    it('empty filters return all tasks', () => {
      fc.assert(
        fc.property(fc.array(arbTask(), { maxLength: 30 }), (tasks) => {
          const result = filterTasks(tasks, {})
          expect(result.length).toBe(tasks.length)
        }),
        { numRuns: 100 },
      )
    })
  })
})
