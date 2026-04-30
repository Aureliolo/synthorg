/**
 * Task mock-data builders.
 *
 * The Kanban board groups tasks by status; ``makeKanbanColumn``
 * provides a convenience builder for a list of tasks in one column.
 *
 * The status enum mirrors the production ``TaskStatus`` from
 * ``web/src/api/types/enums.ts`` (resolved against the OpenAPI
 * schema) so seeded tasks land in the production Kanban columns
 * defined by ``KANBAN_COLUMNS`` in ``web/src/utils/tasks.ts``:
 * ``created`` -> backlog, ``assigned`` -> ready, ``in_progress`` ->
 * in_progress, ``in_review`` -> in_review, ``completed`` -> done,
 * ``blocked`` / ``auth_required`` -> blocked, ``failed`` /
 * ``interrupted`` / ``cancelled`` / ``rejected`` -> terminal.
 */

export type TaskStatus =
  | 'created'
  | 'assigned'
  | 'in_progress'
  | 'in_review'
  | 'completed'
  | 'blocked'
  | 'auth_required'
  | 'failed'
  | 'interrupted'
  | 'cancelled'
  | 'rejected'
  | 'suspended'

export interface MockTask {
  id: string
  title: string
  description: string
  status: TaskStatus
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee_id: string | null
  created_at: string
  approved: boolean
}

export function makeTask(overrides: Partial<MockTask> = {}): MockTask {
  return {
    id: 'task-001',
    title: 'Refactor auth middleware',
    description: 'Tighten the session validation path.',
    status: 'created',
    priority: 'medium',
    assignee_id: 'agent-001',
    created_at: '2026-04-01T12:00:00Z',
    approved: true,
    ...overrides,
  }
}

/**
 * Throw if ``count`` is not a non-negative integer.
 *
 * Without the guard, ``Array.from({ length: count })`` would silently
 * coerce ``-1`` to a 0-length array, ``1.5`` to a 1-length array, and
 * ``NaN`` to a 0-length array, weakening the test signal.
 */
function assertValidCount(count: number, fnName: string): void {
  if (!Number.isInteger(count) || count < 0) {
    throw new RangeError(`${fnName}: count must be a non-negative integer; got ${count}`)
  }
}

export function makeTaskList(count: number = 3): MockTask[] {
  assertValidCount(count, 'makeTaskList')
  return Array.from({ length: count }, (_, idx) =>
    makeTask({
      id: `task-${String(idx + 1).padStart(3, '0')}`,
      title: `Task ${idx + 1}`,
    }),
  )
}

export function makeKanbanColumn(
  status: TaskStatus,
  count: number = 2,
): MockTask[] {
  assertValidCount(count, 'makeKanbanColumn')
  return Array.from({ length: count }, (_, idx) =>
    makeTask({
      id: `task-${status}-${idx + 1}`,
      title: `${status} task ${idx + 1}`,
      status,
    }),
  )
}
