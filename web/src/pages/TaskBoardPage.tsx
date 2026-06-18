import { lazy, Suspense, useState } from 'react'
import { DndContext, DragOverlay, closestCorners } from '@dnd-kit/core'
import { AnimatePresence } from 'motion/react'
import { Target } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { ToggleField } from '@/components/ui/toggle-field'
import { formatNumber } from '@/utils/format'
import { KANBAN_COLUMNS, type TaskBoardFilters } from '@/utils/tasks'
import { TaskBoardSkeleton } from './tasks/TaskBoardSkeleton'
import { TaskColumn } from './tasks/TaskColumn'
import { TaskCard } from './tasks/TaskCard'
import { TaskFilterBar } from './tasks/TaskFilterBar'
import { TaskListView } from './tasks/TaskListView'
import { TaskDetailPanel } from './tasks/TaskDetailPanel'
import { TaskCreateDialog } from './tasks/TaskCreateDialog'
import { SubmitObjectiveDialog } from './tasks/SubmitObjectiveDialog'
import {
  useTaskBoardController,
  type TaskBoardController,
} from './tasks/useTaskBoardController'

const TaskDependencyGraph = lazy(() =>
  import('./tasks/TaskDependencyGraph').then((m) => ({ default: m.TaskDependencyGraph })),
)

export default function TaskBoardPage() {
  const ctrl = useTaskBoardController()
  const [objectiveOpen, setObjectiveOpen] = useState(false)
  if (ctrl.data.loading && ctrl.data.tasks.length === 0) return <TaskBoardSkeleton />

  return (
    <div className="space-y-section-gap">
      <TaskBoardHeader ctrl={ctrl} onSubmitObjective={() => setObjectiveOpen(true)} />
      <TaskBoardBanners ctrl={ctrl} />
      <TaskFilterBar
        filters={ctrl.filters}
        onFiltersChange={ctrl.handleFiltersChange}
        viewMode={ctrl.viewMode}
        onViewModeChange={ctrl.handleViewModeChange}
        onCreateTask={() => ctrl.setCreateOpen(true)}
        assignees={ctrl.assignees}
        taskCount={ctrl.filteredTasks.length}
      />
      <BoardToggles
        showDeps={ctrl.showDeps}
        showTerminal={ctrl.showTerminal}
        onToggleDeps={ctrl.setShowDeps}
        onToggleTerminal={ctrl.setShowTerminal}
      />
      <ActiveFilterChips filters={ctrl.filters} onChange={ctrl.handleFiltersChange} />
      {ctrl.showDeps && (
        <ErrorBoundary level="section">
          <Suspense
            fallback={
              <div className="h-[400px] rounded-lg border border-border bg-surface animate-pulse" />
            }
          >
            <TaskDependencyGraph
              tasks={[...ctrl.filteredTasks]}
              onSelectTask={ctrl.handleSelectTask}
            />
          </Suspense>
        </ErrorBoundary>
      )}
      <ErrorBoundary level="section">
        <TaskBoardContent ctrl={ctrl} />
      </ErrorBoundary>
      <TaskDetailPanelOverlay ctrl={ctrl} />
      <TaskCreateDialog
        open={ctrl.createOpen}
        onOpenChange={ctrl.setCreateOpen}
        onCreate={(payload) => ctrl.data.createTask(payload)}
      />
      <SubmitObjectiveDialog
        open={objectiveOpen}
        onOpenChange={setObjectiveOpen}
        onSubmitted={(submissionId) => {
          // Filter the board to the submission so the spawned root task
          // surfaces here once the async decomposition materialises it.
          ctrl.handleFiltersChange({ ...ctrl.filters, search: submissionId })
        }}
      />
    </div>
  )
}

interface TaskBoardCtrlProps {
  ctrl: TaskBoardController
}

function TaskBoardHeader({
  ctrl,
  onSubmitObjective,
}: TaskBoardCtrlProps & { onSubmitObjective: () => void }) {
  const filteredLen = ctrl.filteredTasks.length
  const totalLen = ctrl.data.tasks.length
  const countLabel =
    filteredLen === totalLen
      ? undefined
      : `${formatNumber(filteredLen)} of ${formatNumber(totalLen)}`
  return (
    <ListHeader
      title="Task Board"
      count={filteredLen}
      countLabel={countLabel}
      secondaryActions={
        <Button variant="outline" size="sm" onClick={onSubmitObjective}>
          <Target className="size-3.5" aria-hidden="true" />
          Submit objective
        </Button>
      }
    />
  )
}

function TaskBoardBanners({ ctrl }: TaskBoardCtrlProps) {
  const showOfflineBanner = !ctrl.data.wsConnected && !ctrl.data.loading
  return (
    <>
      {ctrl.data.error && (
        <ErrorBanner
          severity="error"
          title="Could not load tasks"
          description={ctrl.data.error}
        />
      )}
      {showOfflineBanner && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={
            ctrl.data.wsSetupError ?? 'Data may be stale until the connection recovers.'
          }
        />
      )}
    </>
  )
}

function TaskDetailPanelOverlay({ ctrl }: TaskBoardCtrlProps) {
  const { data } = ctrl
  const shouldShow =
    ctrl.selectedTaskId !== null &&
    data.selectedTask != null &&
    data.selectedTask.id === ctrl.selectedTaskId
  return (
    <AnimatePresence>
      {shouldShow && data.selectedTask && (
        <TaskDetailPanel
          task={data.selectedTask}
          onClose={ctrl.handleClosePanel}
          onUpdate={async (id, payload) => {
            await data.updateTask(id, payload)
          }}
          onTransition={async (id, payload) => {
            await data.transitionTask(id, payload)
          }}
          onCancel={async (id, payload) => (await data.cancelTask(id, payload)) !== null}
          onDelete={(id) => data.deleteTask(id)}
        />
      )}
    </AnimatePresence>
  )
}

interface BoardTogglesProps {
  showDeps: boolean
  showTerminal: boolean
  onToggleDeps: (next: boolean) => void
  onToggleTerminal: (next: boolean) => void
}

function BoardToggles({
  showDeps,
  showTerminal,
  onToggleDeps,
  onToggleTerminal,
}: BoardTogglesProps) {
  return (
    <div className="flex flex-wrap items-center gap-grid-gap">
      <ToggleField label="Dependencies" checked={showDeps} onChange={onToggleDeps} />
      <ToggleField label="Show terminal" checked={showTerminal} onChange={onToggleTerminal} />
    </div>
  )
}

function TaskBoardContent({ ctrl }: TaskBoardCtrlProps) {
  if (ctrl.viewMode === 'list') {
    return (
      <TaskListView tasks={[...ctrl.filteredTasks]} onSelectTask={ctrl.handleSelectTask} />
    )
  }
  const visibleColumns = ctrl.showTerminal
    ? KANBAN_COLUMNS
    : KANBAN_COLUMNS.filter((col) => col.id !== 'terminal')
  return (
    <DndContext
      sensors={ctrl.sensors}
      collisionDetection={closestCorners}
      onDragStart={ctrl.handleDragStart}
      onDragEnd={ctrl.handleDragEnd}
    >
      <div className="flex snap-x snap-mandatory gap-grid-gap overflow-x-auto pb-4 md:snap-none">
        {visibleColumns.map((col) => {
          const columnTasks = ctrl.columns[col.id] ?? []
          const containsSelected =
            ctrl.selectedTaskId !== null &&
            columnTasks.some((t) => t.id === ctrl.selectedTaskId)
          return (
            <TaskColumn
              key={col.id}
              column={col}
              tasks={columnTasks}
              onSelectTask={ctrl.handleSelectTask}
              highlighted={containsSelected}
            />
          )
        })}
      </div>
      <DragOverlay>
        {ctrl.activeTask && (
          <div className="w-72">
            <TaskCard task={ctrl.activeTask} onSelect={() => {}} isOverlay />
          </div>
        )}
      </DragOverlay>
    </DndContext>
  )
}

interface ActiveFilterChipsProps {
  filters: TaskBoardFilters
  onChange: (filters: TaskBoardFilters) => void
}

const FILTER_CHIP_LABELS: Readonly<Record<keyof TaskBoardFilters, string>> = {
  status: 'Status',
  priority: 'Priority',
  assignee: 'Assignee',
  taskType: 'Type',
  search: 'Search',
  dateFrom: 'From',
  dateTo: 'To',
}

function ActiveFilterChips({ filters, onChange }: ActiveFilterChipsProps) {
  const chips = (Object.entries(FILTER_CHIP_LABELS) as [keyof TaskBoardFilters, string][])
    .filter(([key]) => Boolean(filters[key]))
    .map(([key, label]) => ({ key, label: `${label}: ${String(filters[key])}` }))
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
