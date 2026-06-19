import { PRIORITY_VALUES, type Priority, type TaskStatus, type TaskType } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import type { SemanticColor } from '@/utils/agent-status'

/** Narrow a raw string to ``Priority`` by membership, else ``null``. */
export function parsePriority(value: string): Priority | null {
  return (PRIORITY_VALUES as readonly string[]).includes(value) ? (value as Priority) : null
}

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
  status?: TaskStatus | undefined
  priority?: Priority | undefined
  assignee?: string | undefined
  taskType?: TaskType | undefined
  search?: string | undefined
  dateFrom?: string | undefined
  dateTo?: string | undefined
}

type TaskPredicate = (task: Task) => boolean

function _searchPredicate(search: string): TaskPredicate {
  const query = search.toLowerCase()
  return (t) =>
    t.title.toLowerCase().includes(query)
    || t.description.toLowerCase().includes(query)
}

function _dateFromPredicate(from: string): TaskPredicate {
  return (t) => t.deadline != null && t.deadline >= from
}

function _dateToPredicate(rawTo: string): TaskPredicate {
  // A date-only string ("2026-05-24") clamps to end-of-day so a task
  // whose deadline lives anywhere inside that calendar day is included.
  const to = rawTo.includes('T') ? rawTo : `${rawTo}T23:59:59.999Z`
  return (t) => t.deadline != null && t.deadline <= to
}

/**
 * Build the active filter predicates from the caller's filter selection.
 * Each entry in the table corresponds to one optional filter; entries
 * with falsy filter values produce no predicate. Keeping the per-field
 * branching here (not in `filterTasks`) keeps the dispatcher under the
 * complexity cap.
 */
function _buildTaskPredicates(filters: TaskBoardFilters): TaskPredicate[] {
  const preds: TaskPredicate[] = []
  if (filters.status) preds.push((t) => t.status === filters.status)
  if (filters.priority) preds.push((t) => t.priority === filters.priority)
  if (filters.assignee) preds.push((t) => t.assigned_to === filters.assignee)
  if (filters.taskType) preds.push((t) => t.type === filters.taskType)
  if (filters.search) preds.push(_searchPredicate(filters.search))
  if (filters.dateFrom) preds.push(_dateFromPredicate(filters.dateFrom))
  if (filters.dateTo) preds.push(_dateToPredicate(filters.dateTo))
  return preds
}

export function filterTasks(tasks: readonly Task[], filters: TaskBoardFilters): Task[] {
  const preds = _buildTaskPredicates(filters)
  if (preds.length === 0) return [...tasks]
  return tasks.filter((t) => preds.every((p) => p(t)))
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

// ── Status ordering + role constants ────────────────────────

/** Ordered task statuses for Kanban columns. */
export const TASK_STATUS_ORDER: readonly TaskStatus[] = [
  'created',
  'assigned',
  'in_progress',
  'auth_required',
  'in_review',
  'blocked',
  'completed',
  'failed',
  'interrupted',
  'suspended',
  'rejected',
  'cancelled',
] as const

/** Terminal task statuses that cannot transition further. */
export const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set<TaskStatus>([
  'completed',
  'cancelled',
  'rejected',
])

/** Write-capable human roles. */
export const WRITE_ROLES = ['ceo', 'manager', 'pair_programmer'] as const
