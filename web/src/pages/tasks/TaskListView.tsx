import { memo, useCallback, useMemo, useState } from 'react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { PriorityBadge } from '@/components/ui/task-status-indicator'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { EmptyState } from '@/components/ui/empty-state'
import { getTaskTypeLabel } from '@/utils/tasks'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatRelativeTime, formatCurrency } from '@/utils/format'
import { ArrowDown, ArrowUp, Inbox } from 'lucide-react'
import type { DashboardTask } from '@/api/types/tasks'

type SortKey = 'status' | 'title' | 'assignee' | 'priority' | 'type' | 'deadline' | 'cost'
type SortDirection = 'asc' | 'desc'

export interface TaskListViewProps {
  tasks: DashboardTask[]
  onSelectTask: (taskId: string) => void
}

const PRIORITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }

const COLUMNS: { key: SortKey; label: string; width: string; sortable: boolean }[] = [
  { key: 'status', label: 'Status', width: 'w-20', sortable: true },
  { key: 'title', label: 'Title', width: 'flex-1', sortable: true },
  { key: 'assignee', label: 'Assignee', width: 'w-32', sortable: true },
  { key: 'priority', label: 'Priority', width: 'w-24', sortable: true },
  { key: 'type', label: 'Type', width: 'w-24', sortable: true },
  { key: 'deadline', label: 'Deadline', width: 'w-24', sortable: true },
  { key: 'cost', label: 'Cost', width: 'w-20', sortable: true },
]

const SORT_EXTRACTORS: Readonly<
  Record<SortKey, (task: DashboardTask) => string | number>
> = {
  status: (t) => t.status,
  title: (t) => t.title,
  assignee: (t) => t.assigned_to ?? '',
  priority: (t) => PRIORITY_ORDER[t.priority] ?? 9,
  type: (t) => t.type,
  deadline: (t) => t.deadline ?? '',
  cost: (t) => t.cost ?? 0,
}

function compareTasks(
  a: DashboardTask,
  b: DashboardTask,
  key: SortKey,
  dir: SortDirection,
): number {
  const aVal = SORT_EXTRACTORS[key](a)
  const bVal = SORT_EXTRACTORS[key](b)
  const cmp =
    typeof aVal === 'string' && typeof bVal === 'string'
      ? aVal.localeCompare(bVal)
      : (aVal as number) - (bVal as number)
  return dir === 'desc' ? -cmp : cmp
}

function TaskListViewInner({ tasks, onSelectTask }: TaskListViewProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDirection>('asc')

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }, [sortKey])

  const sorted = useMemo(
    () => (sortKey ? [...tasks].sort((a, b) => compareTasks(a, b, sortKey, sortDir)) : tasks),
    [tasks, sortKey, sortDir],
  )

  if (tasks.length === 0) {
    return (
      <EmptyState
        icon={Inbox}
        title="No tasks found"
        description="Try adjusting your filters or create a new task"
      />
    )
  }

  return (
    <div className="rounded-lg border border-border">
      {/* Table header */}
      <div className="flex items-center gap-4 border-b border-border bg-surface px-4 py-2">
        {COLUMNS.map((col) => (
          <button
            key={col.key}
            type="button"
            onClick={() => col.sortable && handleSort(col.key)}
            className={cn(
              'flex items-center gap-1 rounded-sm text-[11px] font-semibold uppercase tracking-wider text-text-muted transition-colors',
              col.sortable && 'cursor-pointer hover:text-foreground',
              col.sortable && FOCUS_RING,
              col.width,
            )}
            aria-sort={sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
          >
            {col.label}
            {sortKey === col.key && (
              sortDir === 'asc'
                ? <ArrowUp className="size-3" aria-hidden="true" />
                : <ArrowDown className="size-3" aria-hidden="true" />
            )}
          </button>
        ))}
      </div>

      {/* Table body */}
      <StaggerGroup className="divide-y divide-border">
        {sorted.map((task) => (
          <StaggerItem key={task.id}>
            <TaskListRow task={task} onSelectTask={onSelectTask} />
          </StaggerItem>
        ))}
      </StaggerGroup>
    </div>
  )
}

export const TaskListView = memo(TaskListViewInner)

interface TaskListRowProps {
  task: DashboardTask
  onSelectTask: (taskId: string) => void
}

const TaskListRow = memo(function TaskListRow({ task, onSelectTask }: TaskListRowProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelectTask(task.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelectTask(task.id)
        }
      }}
      className={cn('flex cursor-pointer items-center gap-4 px-4 py-3 transition-colors hover:bg-card-hover', FOCUS_RING)}
      aria-label={`Task: ${task.title}`}
    >
      <span className="w-20">
        <TaskStatusIndicator status={task.status} label />
      </span>
      <span className="flex-1 truncate text-[13px] font-medium text-foreground">
        {task.title}
      </span>
      <span className="w-32">
        {task.assigned_to ? (
          <span className="flex items-center gap-1.5">
            <Avatar name={task.assigned_to} size="sm" />
            <span className="truncate text-xs text-text-secondary">{task.assigned_to}</span>
          </span>
        ) : (
          <span className="text-xs text-text-muted">Unassigned</span>
        )}
      </span>
      <span className="w-24">
        <PriorityBadge priority={task.priority} />
      </span>
      <span className="w-24 text-xs text-text-secondary">
        {getTaskTypeLabel(task.type)}
      </span>
      <span className="w-24 font-mono text-[10px] text-text-muted">
        {task.deadline ? formatRelativeTime(task.deadline) : '--'}
      </span>
      <span className="w-20 text-right font-mono text-[10px] text-text-muted">
        {task.cost != null ? formatCurrency(task.cost, DEFAULT_CURRENCY) : '--'}
      </span>
    </div>
  )
})
