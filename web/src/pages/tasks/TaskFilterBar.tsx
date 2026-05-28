import { LayoutGrid, List, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { getTaskStatusLabel, getPriorityLabel, getTaskTypeLabel } from '@/utils/tasks'
import type { TaskBoardFilters } from '@/utils/tasks'
import {
  TASK_TYPE_VALUES,
  type Priority,
  type TaskStatus,
  type TaskType,
} from '@/api/types/enums'

const STATUSES: TaskStatus[] = [
  'created',
  'assigned',
  'in_progress',
  'in_review',
  'completed',
  'blocked',
  'failed',
  'interrupted',
  'cancelled',
]

const PRIORITIES: Priority[] = ['critical', 'high', 'medium', 'low']

const SELECT_CLASSES =
  'h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent'
const INPUT_CLASSES =
  'h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent'

export interface TaskFilterBarProps {
  filters: TaskBoardFilters
  onFiltersChange: (filters: TaskBoardFilters) => void
  viewMode: 'board' | 'list'
  onViewModeChange: (mode: 'board' | 'list') => void
  onCreateTask: () => void
  assignees: string[]
  taskCount: number
}

export function TaskFilterBar(props: TaskFilterBarProps) {
  const { filters, onFiltersChange, taskCount } = props
  const hasActiveFilters = computeHasActiveFilters(filters)

  function updateFilter<K extends keyof TaskBoardFilters>(
    key: K,
    value: TaskBoardFilters[K],
  ) {
    onFiltersChange({ ...filters, [key]: value || undefined })
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <TaskFilterControls
          filters={filters}
          assignees={props.assignees}
          updateFilter={updateFilter}
        />
        <span className="text-xs text-text-muted">
          {taskCount} {taskCount === 1 ? 'task' : 'tasks'}
        </span>
        <TaskFilterRightActions
          viewMode={props.viewMode}
          onViewModeChange={props.onViewModeChange}
          onCreateTask={props.onCreateTask}
        />
      </div>
      {hasActiveFilters && (
        <ActiveFilterPills
          filters={filters}
          updateFilter={updateFilter}
          onClearAll={() => onFiltersChange({})}
        />
      )}
    </div>
  )
}

function computeHasActiveFilters(filters: TaskBoardFilters): boolean {
  return Boolean(
    filters.status ||
      filters.priority ||
      filters.assignee ||
      filters.taskType ||
      filters.search ||
      filters.dateFrom ||
      filters.dateTo,
  )
}

interface TaskFilterControlsProps {
  filters: TaskBoardFilters
  assignees: string[]
  updateFilter: <K extends keyof TaskBoardFilters>(key: K, value: TaskBoardFilters[K]) => void
}

function TaskFilterControls({ filters, assignees, updateFilter }: TaskFilterControlsProps) {
  return (
    <>
      <StatusFilter value={filters.status} onValueChange={(v) => updateFilter('status', v)} />
      <PriorityFilter
        value={filters.priority}
        onValueChange={(v) => updateFilter('priority', v)}
      />
      <AssigneeFilter
        value={filters.assignee}
        assignees={assignees}
        onValueChange={(v) => updateFilter('assignee', v)}
      />
      <TypeFilter
        value={filters.taskType}
        onValueChange={(v) => updateFilter('taskType', v)}
      />
      <DateRangeFilters
        dateFrom={filters.dateFrom}
        dateTo={filters.dateTo}
        onFromChange={(v) => updateFilter('dateFrom', v)}
        onToChange={(v) => updateFilter('dateTo', v)}
      />
      <input
        type="text"
        value={filters.search ?? ''}
        onChange={(e) => updateFilter('search', e.target.value || undefined)}
        placeholder="Search tasks..."
        className={`${INPUT_CLASSES} w-48 placeholder:text-text-muted`}
        aria-label="Search tasks"
      />
    </>
  )
}

interface StatusFilterProps {
  value: TaskStatus | undefined
  onValueChange: (value: TaskStatus | undefined) => void
}

function StatusFilter({ value, onValueChange }: StatusFilterProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) =>
        onValueChange((e.target.value || undefined) as TaskStatus | undefined)
      }
      className={SELECT_CLASSES}
      aria-label="Filter by status"
    >
      <option value="">All statuses</option>
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {getTaskStatusLabel(s)}
        </option>
      ))}
    </select>
  )
}

interface PriorityFilterProps {
  value: Priority | undefined
  onValueChange: (value: Priority | undefined) => void
}

function PriorityFilter({ value, onValueChange }: PriorityFilterProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onValueChange((e.target.value || undefined) as Priority | undefined)}
      className={SELECT_CLASSES}
      aria-label="Filter by priority"
    >
      <option value="">All priorities</option>
      {PRIORITIES.map((p) => (
        <option key={p} value={p}>
          {getPriorityLabel(p)}
        </option>
      ))}
    </select>
  )
}

interface AssigneeFilterProps {
  value: string | undefined
  assignees: string[]
  onValueChange: (value: string | undefined) => void
}

function AssigneeFilter({ value, assignees, onValueChange }: AssigneeFilterProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onValueChange(e.target.value || undefined)}
      className={SELECT_CLASSES}
      aria-label="Filter by assignee"
    >
      <option value="">All assignees</option>
      {assignees.map((a) => (
        <option key={a} value={a}>
          {a}
        </option>
      ))}
    </select>
  )
}

interface TypeFilterProps {
  value: TaskType | undefined
  onValueChange: (value: TaskType | undefined) => void
}

function TypeFilter({ value, onValueChange }: TypeFilterProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onValueChange((e.target.value || undefined) as TaskType | undefined)}
      className={SELECT_CLASSES}
      aria-label="Filter by type"
    >
      <option value="">All types</option>
      {TASK_TYPE_VALUES.map((t) => (
        <option key={t} value={t}>
          {getTaskTypeLabel(t)}
        </option>
      ))}
    </select>
  )
}

interface DateRangeFiltersProps {
  dateFrom: string | undefined
  dateTo: string | undefined
  onFromChange: (value: string | undefined) => void
  onToChange: (value: string | undefined) => void
}

function DateRangeFilters({
  dateFrom,
  dateTo,
  onFromChange,
  onToChange,
}: DateRangeFiltersProps) {
  return (
    <>
      <input
        type="date"
        value={dateFrom ?? ''}
        onChange={(e) => onFromChange(e.target.value || undefined)}
        className={INPUT_CLASSES}
        aria-label="Deadline from"
        title="Deadline from"
      />
      <input
        type="date"
        value={dateTo ?? ''}
        onChange={(e) => onToChange(e.target.value || undefined)}
        className={INPUT_CLASSES}
        aria-label="Deadline to"
        title="Deadline to"
      />
    </>
  )
}

interface TaskFilterRightActionsProps {
  viewMode: 'board' | 'list'
  onViewModeChange: (mode: 'board' | 'list') => void
  onCreateTask: () => void
}

function TaskFilterRightActions({
  viewMode,
  onViewModeChange,
  onCreateTask,
}: TaskFilterRightActionsProps) {
  return (
    <div className="ml-auto flex items-center gap-1">
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
      <Button size="sm" onClick={onCreateTask} className="ml-2">
        <Plus className="mr-1 size-4" />
        New Task
      </Button>
    </div>
  )
}

interface ActiveFilterPillsProps {
  filters: TaskBoardFilters
  updateFilter: <K extends keyof TaskBoardFilters>(key: K, value: TaskBoardFilters[K]) => void
  onClearAll: () => void
}

function ActiveFilterPills({ filters, updateFilter, onClearAll }: ActiveFilterPillsProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {filters.status && (
        <FilterPill
          label={`Status: ${getTaskStatusLabel(filters.status)}`}
          onRemove={() => updateFilter('status', undefined)}
        />
      )}
      {filters.priority && (
        <FilterPill
          label={`Priority: ${getPriorityLabel(filters.priority)}`}
          onRemove={() => updateFilter('priority', undefined)}
        />
      )}
      {filters.assignee && (
        <FilterPill
          label={`Assignee: ${filters.assignee}`}
          onRemove={() => updateFilter('assignee', undefined)}
        />
      )}
      {filters.taskType && (
        <FilterPill
          label={`Type: ${getTaskTypeLabel(filters.taskType)}`}
          onRemove={() => updateFilter('taskType', undefined)}
        />
      )}
      {filters.dateFrom && (
        <FilterPill
          label={`From: ${filters.dateFrom}`}
          onRemove={() => updateFilter('dateFrom', undefined)}
        />
      )}
      {filters.dateTo && (
        <FilterPill
          label={`To: ${filters.dateTo}`}
          onRemove={() => updateFilter('dateTo', undefined)}
        />
      )}
      {filters.search && (
        <FilterPill
          label={`Search: "${filters.search}"`}
          onRemove={() => updateFilter('search', undefined)}
        />
      )}
      <button
        type="button"
        onClick={onClearAll}
        className="text-xs text-text-muted hover:text-foreground transition-colors"
      >
        Clear all
      </button>
    </div>
  )
}

interface FilterPillProps {
  label: string
  onRemove: () => void
}

function FilterPill({ label, onRemove }: FilterPillProps) {
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
