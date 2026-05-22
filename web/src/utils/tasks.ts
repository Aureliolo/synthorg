import type { Priority, TaskStatus, TaskType } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import type { SemanticColor } from '@/lib/utils'

// ── Status color mapping ────────────────────────────────────

const TASK_STATUS_COLOR_MAP: Record<TaskStatus, SemanticColor | 'text-secondary'> = {
  created: 'text-secondary',
  assigned: 'accent',
  in_progress: 'accent',
  in_review: 'warning',
  completed: 'success',
  blocked: 'danger',
  failed: 'danger',
  interrupted: 'warning',
  suspended: 'warning',
  cancelled: 'text-secondary',
  rejected: 'danger',
  auth_required: 'warning',
}

export function getTaskStatusColor(status: TaskStatus): SemanticColor | 'text-secondary' {
  return TASK_STATUS_COLOR_MAP[status]
}

// ── Status labels ───────────────────────────────────────────

const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  created: 'Created',
  assigned: 'Assigned',
  in_progress: 'In Progress',
  in_review: 'In Review',
  completed: 'Completed',
  blocked: 'Blocked',
  failed: 'Failed',
  interrupted: 'Interrupted',
  suspended: 'Suspended',
  cancelled: 'Cancelled',
  rejected: 'Rejected',
  auth_required: 'Auth Required',
}

export function getTaskStatusLabel(status: TaskStatus): string {
  return TASK_STATUS_LABELS[status]
}

// ── Priority color mapping ──────────────────────────────────

const PRIORITY_COLOR_MAP: Record<Priority, SemanticColor | 'text-secondary'> = {
  critical: 'danger',
  high: 'warning',
  medium: 'accent',
  low: 'text-secondary',
}

export function getPriorityColor(priority: Priority): SemanticColor | 'text-secondary' {
  return PRIORITY_COLOR_MAP[priority]
}

// ── Priority labels ─────────────────────────────────────────

const PRIORITY_LABELS: Record<Priority, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export function getPriorityLabel(priority: Priority): string {
  return PRIORITY_LABELS[priority]
}

// ── Task type labels ────────────────────────────────────────

const TASK_TYPE_LABELS: Record<TaskType, string> = {
  development: 'Development',
  design: 'Design',
  research: 'Research',
  review: 'Review',
  meeting: 'Meeting',
  admin: 'Admin',
  analysis: 'Analysis',
}

export function getTaskTypeLabel(type: TaskType): string {
  return TASK_TYPE_LABELS[type]
}

// ── Kanban column definitions ───────────────────────────────

export type KanbanColumnId =
  | 'backlog'
  | 'ready'
  | 'in_progress'
  | 'in_review'
  | 'done'
  | 'blocked'
  | 'terminal'

export interface KanbanColumn {
  readonly id: KanbanColumnId
  readonly label: string
  readonly statuses: readonly TaskStatus[]
  readonly color: SemanticColor | 'text-secondary'
}

export const KANBAN_COLUMNS: readonly KanbanColumn[] = [
  { id: 'backlog', label: 'Backlog', statuses: ['created'], color: 'text-secondary' },
  { id: 'ready', label: 'Ready', statuses: ['assigned'], color: 'accent' },
  { id: 'in_progress', label: 'In Progress', statuses: ['in_progress'], color: 'accent' },
  { id: 'in_review', label: 'In Review', statuses: ['in_review'], color: 'warning' },
  { id: 'done', label: 'Done', statuses: ['completed'], color: 'success' },
  { id: 'blocked', label: 'Blocked', statuses: ['blocked', 'auth_required'], color: 'danger' },
  { id: 'terminal', label: 'Terminal', statuses: ['failed', 'interrupted', 'cancelled', 'rejected'], color: 'text-secondary' },
] as const

/** Off-board statuses not displayed on the Kanban board (resumable). */
export const OFF_BOARD_STATUSES: ReadonlySet<TaskStatus> = new Set(['suspended'])

export const STATUS_TO_COLUMN: Record<TaskStatus, KanbanColumnId | null> = {
  ...Object.fromEntries(
    KANBAN_COLUMNS.flatMap((col) =>
      col.statuses.map((status) => [status, col.id]),
    ),
  ),
  ...Object.fromEntries([...OFF_BOARD_STATUSES].map((s) => [s, null])),
} as Record<TaskStatus, KanbanColumnId | null>

// ── Group tasks by column ───────────────────────────────────

export function groupTasksByColumn(tasks: readonly Task[]): Record<KanbanColumnId, Task[]> {
  const grouped: Record<KanbanColumnId, Task[]> = {
    backlog: [],
    ready: [],
    in_progress: [],
    in_review: [],
    done: [],
    blocked: [],
    terminal: [],
  }

  for (const task of tasks) {
    const columnId = STATUS_TO_COLUMN[task.status]
    if (columnId) {
      grouped[columnId].push(task)
    }
  }

  return grouped
}

// ── Client-side filtering ───────────────────────────────────

export interface TaskBoardFilters {
  status?: TaskStatus
  priority?: Priority
  assignee?: string
  taskType?: TaskType
  search?: string
  dateFrom?: string
  dateTo?: string
}

export function filterTasks(tasks: readonly Task[], filters: TaskBoardFilters): Task[] {
  let result = tasks as Task[]

  if (filters.status) {
    result = result.filter((t) => t.status === filters.status)
  }

  if (filters.priority) {
    result = result.filter((t) => t.priority === filters.priority)
  }

  if (filters.assignee) {
    result = result.filter((t) => t.assigned_to === filters.assignee)
  }

  if (filters.taskType) {
    result = result.filter((t) => t.type === filters.taskType)
  }

  if (filters.search) {
    const query = filters.search.toLowerCase()
    result = result.filter(
      (t) =>
        t.title.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query),
    )
  }

  if (filters.dateFrom) {
    const from = filters.dateFrom
    result = result.filter((t) => t.deadline && t.deadline >= from)
  }

  if (filters.dateTo) {
    const to = filters.dateTo.includes('T') ? filters.dateTo : filters.dateTo + 'T23:59:59.999Z'
    result = result.filter((t) => t.deadline && t.deadline <= to)
  }

  return result
}

// ── Status transition validation ────────────────────────────

export const VALID_TRANSITIONS: Record<TaskStatus, readonly TaskStatus[]> = {
  created: ['assigned', 'rejected'],
  assigned: ['in_progress', 'auth_required', 'failed', 'blocked', 'cancelled', 'interrupted', 'suspended'],
  in_progress: ['in_review', 'auth_required', 'blocked', 'failed', 'cancelled', 'interrupted', 'suspended'],
  in_review: ['completed', 'in_progress', 'blocked', 'cancelled'],
  completed: [],
  blocked: ['assigned'],
  failed: ['assigned'],
  interrupted: ['assigned'],
  suspended: ['assigned'],
  cancelled: [],
  rejected: [],
  auth_required: ['assigned', 'cancelled'],
}

export function canTransitionTo(currentStatus: TaskStatus, targetStatus: TaskStatus): boolean {
  return VALID_TRANSITIONS[currentStatus].includes(targetStatus)
}

export function getAvailableTransitions(status: TaskStatus): readonly TaskStatus[] {
  return VALID_TRANSITIONS[status]
}
