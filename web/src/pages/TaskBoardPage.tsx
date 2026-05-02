import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  closestCorners,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { AnimatePresence } from 'motion/react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { useRegisterShortcuts } from '@/hooks/use-shortcut-registry'
import { useTaskBoardData } from '@/hooks/useTaskBoardData'
import { useOptimisticUpdate } from '@/hooks/useOptimisticUpdate'
import { useToastStore } from '@/stores/toast'
import { formatNumber } from '@/utils/format'
import {
  type TaskBoardFilters,
  KANBAN_COLUMNS,
  filterTasks,
  groupTasksByColumn,
  canTransitionTo,
} from '@/utils/tasks'
import { TaskBoardSkeleton } from './tasks/TaskBoardSkeleton'
import { TaskColumn } from './tasks/TaskColumn'
import { TaskCard } from './tasks/TaskCard'
import { TaskFilterBar } from './tasks/TaskFilterBar'
import { TaskListView } from './tasks/TaskListView'
import { TaskDetailPanel } from './tasks/TaskDetailPanel'
import { TaskCreateDialog } from './tasks/TaskCreateDialog'
import type { Priority, TaskStatus, TaskType } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'

const TaskDependencyGraph = lazy(() => import('./tasks/TaskDependencyGraph').then((m) => ({ default: m.TaskDependencyGraph })))

export default function TaskBoardPage() {
  const {
    tasks,
    selectedTask,
    loading,
    error,
    wsConnected,
    wsSetupError,
    fetchTask,
    createTask,
    updateTask,
    transitionTask,
    cancelTask,
    deleteTask,
    optimisticTransition,
  } = useTaskBoardData()

  const [searchParams, setSearchParams] = useSearchParams()
  const [createOpen, setCreateOpen] = useState(false)
  const [showTerminal, setShowTerminal] = useState(false)
  const [showDeps, setShowDeps] = useState(false)
  const [activeTask, setActiveTask] = useState<Task | null>(null)

  const { execute: executeOptimistic } = useOptimisticUpdate()

  // Parse URL params
  const viewMode = searchParams.get('view') === 'list' ? 'list' : 'board'
  const selectedTaskId = searchParams.get('selected')

  // Sync selectedTaskId from URL with store (handles direct navigation / shared links)
  const prevSelectedRef = useRef<string | null>(null)
  const skipNextFetchRef = useRef(false)
  useEffect(() => {
    if (selectedTaskId && selectedTaskId !== prevSelectedRef.current) {
      if (skipNextFetchRef.current) {
        skipNextFetchRef.current = false
      } else {
        fetchTask(selectedTaskId)
      }
    }
    prevSelectedRef.current = selectedTaskId
  }, [selectedTaskId, fetchTask])

  const filters: TaskBoardFilters = useMemo(() => ({
    status: (searchParams.get('status') as TaskStatus) || undefined,
    priority: (searchParams.get('priority') as Priority) || undefined,
    assignee: searchParams.get('assignee') || undefined,
    taskType: (searchParams.get('type') as TaskType) || undefined,
    search: searchParams.get('search') || undefined,
    dateFrom: searchParams.get('dateFrom') || undefined,
    dateTo: searchParams.get('dateTo') || undefined,
  }), [searchParams])

  // Client-side filtering
  const filteredTasks = useMemo(() => filterTasks(tasks, filters), [tasks, filters])

  // Kanban grouping
  const columns = useMemo(() => groupTasksByColumn(filteredTasks), [filteredTasks])

  // Unique assignees for filter dropdown
  const assignees = useMemo(() => {
    const set = new Set<string>()
    for (const task of tasks) {
      if (task.assigned_to) set.add(task.assigned_to)
    }
    return Array.from(set).sort()
  }, [tasks])

  // Filter handling
  const handleFiltersChange = useCallback((newFilters: TaskBoardFilters) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      // Preserve non-filter params
      const view = next.get('view')
      const selected = next.get('selected')
      // Clear all filter params
      next.delete('status')
      next.delete('priority')
      next.delete('assignee')
      next.delete('type')
      next.delete('search')
      next.delete('dateFrom')
      next.delete('dateTo')
      // Set new filter params
      if (newFilters.status) next.set('status', newFilters.status)
      if (newFilters.priority) next.set('priority', newFilters.priority)
      if (newFilters.assignee) next.set('assignee', newFilters.assignee)
      if (newFilters.taskType) next.set('type', newFilters.taskType)
      if (newFilters.search) next.set('search', newFilters.search)
      if (newFilters.dateFrom) next.set('dateFrom', newFilters.dateFrom)
      if (newFilters.dateTo) next.set('dateTo', newFilters.dateTo)
      if (view) next.set('view', view)
      if (selected) next.set('selected', selected)
      return next
    })
  }, [setSearchParams])

  const handleViewModeChange = useCallback((mode: 'board' | 'list') => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (mode === 'list') {
        next.set('view', 'list')
      } else {
        next.delete('view')
      }
      return next
    })
  }, [setSearchParams])

  // Task selection
  const handleSelectTask = useCallback((taskId: string) => {
    skipNextFetchRef.current = true
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('selected', taskId)
      return next
    })
    fetchTask(taskId)
  }, [setSearchParams, fetchTask])

  const handleClosePanel = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('selected')
      return next
    })
  }, [setSearchParams])

  // DnD
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor),
  )

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const task = (event.active.data.current as { task?: Task })?.task
    if (task) setActiveTask(task)
  }, [])

  const handleDragEnd = useCallback(async (event: DragEndEvent) => {
    setActiveTask(null)
    const { active, over } = event
    if (!over) return

    const taskId = active.id as string
    const targetColumnId = over.id as string
    const targetColumn = KANBAN_COLUMNS.find((col) => col.id === targetColumnId)
    if (!targetColumn) return

    const targetStatus = targetColumn.statuses[0]
    if (!targetStatus) return

    const sourceTask = tasks.find((t) => t.id === taskId)
    if (!sourceTask || sourceTask.status === targetStatus) return

    if (!canTransitionTo(sourceTask.status, targetStatus)) {
      useToastStore.getState().add({
        variant: 'warning',
        title: 'Invalid transition',
        description: `Cannot move from "${sourceTask.status}" to "${targetStatus}".`,
      })
      return
    }

    const result = await executeOptimistic(
      () => optimisticTransition(taskId, targetStatus),
      () => transitionTask(taskId, { target_status: targetStatus, expected_version: sourceTask.version }),
    )
    if (result === null) {
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not move task',
        description: 'It may have changed status. Refresh and try again.',
      })
    }
  }, [tasks, optimisticTransition, transitionTask, executeOptimistic])

  // Create task. ``createTask`` is sentinel-returning (``Task`` on
  // success, ``null`` on failure) and owns both the success/error
  // toasts. Forward the sentinel so the dialog can decide whether to
  // close (keep open on ``null``) without wrapping this in try/catch.
  const handleCreateTask = useCallback(
    async (data: Parameters<typeof createTask>[0]) => createTask(data),
    [createTask],
  )

  // Register documented keyboard shortcuts for the command cheatsheet.
  useRegisterShortcuts([
    { keys: ['D'], label: 'Toggle dependencies overlay', group: 'Task board' },
    { keys: ['T'], label: 'Toggle terminal columns', group: 'Task board' },
    { keys: ['V'], label: 'Cycle view mode (board / list)', group: 'Task board' },
  ])

  // Wire the actual handlers. No-op when focus is inside a form input
  // (textbox, textarea, contenteditable) or any modifier key is held.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      // Ignore auto-repeat from held keys; each discrete press should
      // toggle once rather than rapidly flip back and forth.
      if (event.repeat) return
      const target = event.target
      if (target instanceof HTMLElement) {
        const tag = target.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
        if (target.isContentEditable) return
      }
      const key = event.key.toUpperCase()
      if (key === 'D') {
        event.preventDefault()
        setShowDeps((current) => !current)
      } else if (key === 'T') {
        event.preventDefault()
        setShowTerminal((current) => !current)
      } else if (key === 'V') {
        event.preventDefault()
        handleViewModeChange(viewMode === 'board' ? 'list' : 'board')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewMode, handleViewModeChange])

  // Skeleton on initial load
  if (loading && tasks.length === 0) {
    return <TaskBoardSkeleton />
  }

  const visibleColumns = showTerminal
    ? KANBAN_COLUMNS
    : KANBAN_COLUMNS.filter((col) => col.id !== 'terminal')

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Task Board"
        count={filteredTasks.length}
        countLabel={
          filteredTasks.length === tasks.length
            ? undefined
            : `${formatNumber(filteredTasks.length)} of ${formatNumber(tasks.length)}`
        }
        secondaryActions={
          <>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={showDeps}
                onChange={(e) => setShowDeps(e.target.checked)}
                className="rounded border-border"
              />
              Dependencies
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={showTerminal}
                onChange={(e) => setShowTerminal(e.target.checked)}
                className="rounded border-border"
              />
              Show terminal
            </label>
          </>
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load tasks" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <TaskFilterBar
        filters={filters}
        onFiltersChange={handleFiltersChange}
        viewMode={viewMode}
        onViewModeChange={handleViewModeChange}
        onCreateTask={() => setCreateOpen(true)}
        assignees={assignees}
        taskCount={filteredTasks.length}
      />

      {/* Active filter chips: surface every applied filter as a
          removable pill so the operator can see what's narrowing
          the board and clear individual entries without resetting
          everything. Click-to-remove, plus a "Clear all" affordance
          when more than one filter is active. */}
      <ActiveFilterChips filters={filters} onChange={handleFiltersChange} />

      {showDeps && (
        <ErrorBoundary level="section">
          <Suspense fallback={<div className="h-[400px] rounded-lg border border-border bg-surface animate-pulse" />}>
            <TaskDependencyGraph tasks={filteredTasks} onSelectTask={handleSelectTask} />
          </Suspense>
        </ErrorBoundary>
      )}

      <ErrorBoundary level="section">
        {viewMode === 'board' ? (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCorners}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
          >
            <div className="flex snap-x snap-mandatory gap-4 overflow-x-auto pb-4 md:snap-none">
              {/* Each column is min-width-280 so narrow viewports scroll horizontally; keeps drag-drop working at every breakpoint. */}
              {visibleColumns.map((col) => {
                const columnTasks = columns[col.id] ?? []
                const containsSelected =
                  selectedTaskId !== null
                  && columnTasks.some((t) => t.id === selectedTaskId)
                return (
                  <TaskColumn
                    key={col.id}
                    column={col}
                    tasks={columnTasks}
                    onSelectTask={handleSelectTask}
                    highlighted={containsSelected}
                  />
                )
              })}
            </div>
            <DragOverlay>
              {activeTask && (
                <div className="w-72">
                  <TaskCard task={activeTask} onSelect={() => {}} isOverlay />
                </div>
              )}
            </DragOverlay>
          </DndContext>
        ) : (
          <TaskListView
            tasks={filteredTasks}
            onSelectTask={handleSelectTask}
          />
        )}
      </ErrorBoundary>

      {/* Detail panel overlay */}
      <AnimatePresence>
        {selectedTaskId && selectedTask && selectedTask.id === selectedTaskId && (
          <TaskDetailPanel
            task={selectedTask}
            onClose={handleClosePanel}
            onUpdate={async (id, data) => { await updateTask(id, data) }}
            onTransition={async (id, data) => { await transitionTask(id, data) }}
            onCancel={async (id, data) => (await cancelTask(id, data)) !== null}
            onDelete={(id) => deleteTask(id)}
          />
        )}
      </AnimatePresence>

      {/* Create dialog */}
      <TaskCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreate={handleCreateTask}
      />
    </div>
  )
}

interface ActiveFilterChipsProps {
  filters: TaskBoardFilters
  onChange: (filters: TaskBoardFilters) => void
}

/**
 * Render the currently-applied TaskBoardFilters as removable chips.
 * Hidden when no filters are active. Each chip's X button removes
 * just that filter; the trailing "Clear all" appears once two or
 * more are active so the operator can reset the whole bar in one
 * click.
 */
function ActiveFilterChips({ filters, onChange }: ActiveFilterChipsProps) {
  const chips: { key: keyof TaskBoardFilters; label: string }[] = []
  if (filters.status) chips.push({ key: 'status', label: `Status: ${filters.status}` })
  if (filters.priority) chips.push({ key: 'priority', label: `Priority: ${filters.priority}` })
  if (filters.assignee) chips.push({ key: 'assignee', label: `Assignee: ${filters.assignee}` })
  if (filters.taskType) chips.push({ key: 'taskType', label: `Type: ${filters.taskType}` })
  if (filters.search) chips.push({ key: 'search', label: `Search: ${filters.search}` })
  if (filters.dateFrom) chips.push({ key: 'dateFrom', label: `From: ${filters.dateFrom}` })
  if (filters.dateTo) chips.push({ key: 'dateTo', label: `To: ${filters.dateTo}` })
  if (chips.length === 0) return null

  return (
    <div
      role="region"
      aria-label="Active filters"
      className="flex flex-wrap items-center gap-2"
    >
      <span className="text-xs text-text-secondary">
        {formatNumber(chips.length)} active filter{chips.length === 1 ? '' : 's'}:
      </span>
      {chips.map((chip) => (
        <button
          key={chip.key}
          type="button"
          onClick={() => onChange({ ...filters, [chip.key]: undefined })}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-0.5 text-xs text-foreground transition-colors hover:bg-card-hover"
        >
          <span>{chip.label}</span>
          <span aria-hidden="true" className="text-text-secondary">
            ×
          </span>
          <span className="sr-only">Remove {chip.label}</span>
        </button>
      ))}
      {chips.length > 1 && (
        <button
          type="button"
          onClick={() => onChange({})}
          className="text-xs text-accent hover:underline"
        >
          Clear all
        </button>
      )}
    </div>
  )
}
