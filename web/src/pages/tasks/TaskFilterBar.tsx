import { LayoutGrid, List, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { getTaskStatusLabel, getPriorityLabel, getTaskTypeLabel } from '@/utils/tasks'
import type { TaskBoardFilters } from '@/utils/tasks'
import { TASK_TYPE_VALUES, type Priority, type TaskStatus, type TaskType } from '@/api/types/enums'

const STATUSES: TaskStatus[] = [
  'created', 'assigned', 'in_progress', 'in_review', 'completed',
  'blocked', 'failed', 'interrupted', 'cancelled',
]

const PRIORITIES: Priority[] = ['critical', 'high', 'medium', 'low']

const TASK_TYPES = TASK_TYPE_VALUES

export interface TaskFilterBarProps {
  filters: TaskBoardFilters
  onFiltersChange: (filters: TaskBoardFilters) => void
  viewMode: 'board' | 'list'
  onViewModeChange: (mode: 'board' | 'list') => void
  onCreateTask: () => void
  assignees: string[]
  taskCount: number
}

export function TaskFilterBar({
  filters,
  onFiltersChange,
  viewMode,
  onViewModeChange,
  onCreateTask,
  assignees,
  taskCount,
}: TaskFilterBarProps) {
  const hasActiveFilters = !!(filters.status || filters.priority || filters.assignee || filters.taskType || filters.search || filters.dateFrom || filters.dateTo)

  function updateFilter<K extends keyof TaskBoardFilters>(key: K, value: TaskBoardFilters[K]) {
    onFiltersChange({ ...filters, [key]: value || undefined })
  }

  function clearFilters() {
    onFiltersChange({})
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {/* Status filter */}
        <select
          value={filters.status ?? ''}
          onChange={(e) => updateFilter('status', (e.target.value || undefined) as TaskStatus | undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{getTaskStatusLabel(s)}</option>
          ))}
        </select>

        {/* Priority filter */}
        <select
          value={filters.priority ?? ''}
          onChange={(e) => updateFilter('priority', (e.target.value || undefined) as Priority | undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by priority"
        >
          <option value="">All priorities</option>
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>{getPriorityLabel(p)}</option>
          ))}
        </select>

        {/* Assignee filter */}
        <select
          value={filters.assignee ?? ''}
          onChange={(e) => updateFilter('assignee', e.target.value || undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by assignee"
        >
          <option value="">All assignees</option>
          {assignees.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>

        {/* Task type filter */}
        <select
          value={filters.taskType ?? ''}
          onChange={(e) => updateFilter('taskType', (e.target.value || undefined) as TaskType | undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by type"
        >
          <option value="">All types</option>
          {TASK_TYPES.map((t) => (
            <option key={t} value={t}>{getTaskTypeLabel(t)}</option>
          ))}
        </select>

        {/* Date range */}
        <input
          type="date"
          value={filters.dateFrom ?? ''}
          onChange={(e) => updateFilter('dateFrom', e.target.value || undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Deadline from"
          title="Deadline from"
        />
        <input
          type="date"
          value={filters.dateTo ?? ''}
          onChange={(e) => updateFilter('dateTo', e.target.value || undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Deadline to"
          title="Deadline to"
        />

        {/* Search */}
        <input
          type="text"
          value={filters.search ?? ''}
          onChange={(e) => updateFilter('search', e.target.value || undefined)}
          placeholder="Search tasks..."
          className="h-8 w-48 rounded-md border border-border bg-surface px-2 text-xs text-foreground placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Search tasks"
        />

        {/* Task count */}
        <span className="text-xs text-text-muted">
          {taskCount} {taskCount === 1 ? 'task' : 'tasks'}
        </span>

        {/* Spacer */}
        <div className="ml-auto flex items-center gap-1">
          {/* View toggle */}
          <Button
            variant={viewMode === 'board' ? 'secondary' : 'ghost'}
            size="icon"
            onClick={() => onViewModeChange('board')}
            aria-label="Board view"
            aria-pressed={viewMode === 'board'}
          >
            <LayoutGrid className="size-4" />
          </Button>
          <Button
            variant={viewMode === 'list' ? 'secondary' : 'ghost'}
            size="icon"
            onClick={() => onViewModeChange('list')}
            aria-label="List view"
            aria-pressed={viewMode === 'list'}
          >
            <List className="size-4" />
          </Button>

          {/* Create task */}
          <Button size="sm" onClick={onCreateTask} className="ml-2">
            <Plus className="mr-1 size-4" />
            New Task
          </Button>
        </div>
      </div>

      {/* Active filter pills */}
      {hasActiveFilters && (
        <div className="flex flex-wrap items-center gap-1.5">
          {filters.status && (
            <FilterPill label={`Status: ${getTaskStatusLabel(filters.status)}`} onRemove={() => updateFilter('status', undefined)} />
          )}
          {filters.priority && (
            <FilterPill label={`Priority: ${getPriorityLabel(filters.priority)}`} onRemove={() => updateFilter('priority', undefined)} />
          )}
          {filters.assignee && (
            <FilterPill label={`Assignee: ${filters.assignee}`} onRemove={() => updateFilter('assignee', undefined)} />
          )}
          {filters.taskType && (
            <FilterPill label={`Type: ${getTaskTypeLabel(filters.taskType)}`} onRemove={() => updateFilter('taskType', undefined)} />
          )}
          {filters.dateFrom && (
            <FilterPill label={`From: ${filters.dateFrom}`} onRemove={() => updateFilter('dateFrom', undefined)} />
          )}
          {filters.dateTo && (
            <FilterPill label={`To: ${filters.dateTo}`} onRemove={() => updateFilter('dateTo', undefined)} />
          )}
          {filters.search && (
            <FilterPill label={`Search: "${filters.search}"`} onRemove={() => updateFilter('search', undefined)} />
          )}
          <button
            type="button"
            onClick={clearFilters}
            className="text-xs text-text-muted hover:text-foreground transition-colors"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

function FilterPill({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-[10px] text-text-secondary">
      {label}
      <button
        type="button"
        onClick={onRemove}
        className="ml-0.5 rounded-full p-0.5 hover:bg-border transition-colors"
        aria-label={`Remove filter: ${label}`}
      >
        <X className="size-2.5" />
      </button>
    </span>
  )
}
