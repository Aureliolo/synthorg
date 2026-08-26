import { memo, useCallback, useMemo, useState, type ReactNode } from 'react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { Checkbox } from '@/components/ui/checkbox'
import {
  PriorityBadge,
  TaskStatusIndicator,
} from '@/components/ui/task-status-indicator'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { EmptyState } from '@/components/ui/empty-state'
import { getTaskTypeLabel } from '@/utils/tasks'
import { UNASSIGNED_LABEL, UNKNOWN_AGENT_NAME } from '@/utils/agents'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatRelativeTime, formatDateTime, formatCurrency } from '@/utils/format'
import { ArrowDown, ArrowUp, Inbox } from 'lucide-react'
import type { DashboardTask } from '@/api/types/tasks'

type SortKey = 'status' | 'title' | 'assignee' | 'priority' | 'type' | 'deadline' | 'cost'
type SortDirection = 'asc' | 'desc'

export interface TaskListViewProps {
  // Accept readonly so callers pass the store array directly (it is spread
  // internally for sorting); a defensive copy at the call site would defeat the
  // component's React.memo on every parent render.
  tasks: readonly DashboardTask[]
  onSelectTask: (taskId: string) => void
  /** When defined, every row carries a selection checkbox. */
  onToggleSelect?: ((taskId: string) => void) | undefined
  selectedIds?: ReadonlySet<string> | undefined
  /**
   * What to show with no rows. The page supplies it, because only the page
   * knows whether a filter emptied the list or the org has no tasks at all,
   * and telling an operator with no filters set to adjust their filters names
   * something they cannot act on.
   */
  emptyNode?: ReactNode
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

/** What the assignee cell prints, which is what the column sorts by. */
function assigneeLabel(task: DashboardTask): string {
  if (!task.assigned_to) return UNASSIGNED_LABEL
  return task.assigned_to_name ?? UNKNOWN_AGENT_NAME
}

const SORT_EXTRACTORS: Readonly<
  Record<SortKey, (task: DashboardTask) => string | number>
> = {
  status: (t) => t.status,
  title: (t) => t.title,
  // The rendered label, fallbacks included: sorting both "unassigned" and
  // "assigned to someone unresolvable" as the empty string filed them
  // together at one end while the rows read as two different things.
  assignee: assigneeLabel,
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

/** Accessible name for a sortable header button, conveying the sort state. */
function sortLabel(
  label: string,
  sortable: boolean,
  active: boolean,
  dir: SortDirection,
): string | undefined {
  if (!sortable) return undefined
  if (active) {
    return `${label}, sorted ${dir === 'asc' ? 'ascending' : 'descending'}. Activate to reverse the sort order.`
  }
  return `${label}, not sorted. Activate to sort by this column.`
}

function TaskListViewInner({
  tasks,
  onSelectTask,
  onToggleSelect,
  selectedIds,
  emptyNode,
}: TaskListViewProps) {
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
    if (emptyNode !== undefined) return <>{emptyNode}</>
    return (
      <EmptyState
        icon={Inbox}
        title="No tasks yet"
        description="Create a task or let the org generate work to populate the board."
      />
    )
  }

  return (
    // overflow-x-auto + min-w keep the fixed-width columns from clipping or
    // forcing whole-page horizontal scroll at tablet widths (768-1023px).
    <div className="overflow-x-auto rounded-lg border border-border">
      <div className="min-w-[44rem]">
      {/* Table header */}
      <div className="flex items-center gap-4 border-b border-border bg-surface px-4 py-2">
        {/* Holds the checkbox column's width so the headings stay over their
            own cells once selection is on. */}
        {onToggleSelect && <span className="w-4 shrink-0" aria-hidden="true" />}
        {COLUMNS.map((col) => (
          <button
            key={col.key}
            type="button"
            disabled={!col.sortable}
            onClick={() => col.sortable && handleSort(col.key)}
            className={cn(
              'flex items-center gap-1 rounded-sm text-[11px] font-semibold uppercase tracking-wider text-text-muted transition-colors',
              col.sortable && 'cursor-pointer hover:text-foreground',
              col.sortable && FOCUS_RING,
              col.width,
            )}
            // aria-sort is ignored on a button (valid only on columnheader);
            // this list is a row-button widget, not a table, so the sort state
            // is announced through the button's accessible name instead.
            aria-label={sortLabel(col.label, col.sortable, sortKey === col.key, sortDir)}
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
            <TaskListRow
              task={task}
              onSelectTask={onSelectTask}
              onToggleSelect={onToggleSelect}
              selected={selectedIds?.has(task.id) ?? false}
            />
          </StaggerItem>
        ))}
      </StaggerGroup>
      </div>
    </div>
  )
}

export const TaskListView = memo(TaskListViewInner)

interface TaskListRowProps {
  task: DashboardTask
  onSelectTask: (taskId: string) => void
  onToggleSelect?: ((taskId: string) => void) | undefined
  selected?: boolean | undefined
}

const TaskListRow = memo(function TaskListRow({
  task,
  onSelectTask,
  onToggleSelect,
  selected = false,
}: TaskListRowProps) {
  return (
    <div className={cn('flex items-center gap-4 pl-4', selected && 'bg-accent/5')}>
      {/* A sibling of the row, never a child of it: the row is itself a
          button, and a control inside one is invalid markup that behaves
          unpredictably for assistive technology and for a plain click. */}
      {onToggleSelect && (
        <Checkbox
          checked={selected}
          onCheckedChange={() => onToggleSelect(task.id)}
          aria-label={`Select task ${task.title}`}
        />
      )}
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
      className={cn('flex flex-1 cursor-pointer items-center gap-4 py-3 pr-4 transition-colors hover:bg-card-hover', FOCUS_RING)}
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
            <Avatar name={assigneeLabel(task)} size="sm" />
            <span className="truncate text-xs text-text-secondary">
              {assigneeLabel(task)}
            </span>
          </span>
        ) : (
          <span className="text-xs text-text-muted">{UNASSIGNED_LABEL}</span>
        )}
      </span>
      <span className="w-24">
        <PriorityBadge priority={task.priority} />
      </span>
      <span className="w-24 text-xs text-text-secondary">
        {getTaskTypeLabel(task.type)}
      </span>
      <span className="w-24 font-mono text-[10px] text-text-muted">
        {task.deadline ? (
          <time dateTime={task.deadline} title={formatDateTime(task.deadline)}>
            {formatRelativeTime(task.deadline)}
          </time>
        ) : (
          '--'
        )}
      </span>
      <span className="w-20 text-right font-mono text-[10px] text-text-muted">
        {task.cost != null ? formatCurrency(task.cost, DEFAULT_CURRENCY) : '--'}
      </span>
    </div>
    </div>
  )
})
