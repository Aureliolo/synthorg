import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { Loader2 } from 'lucide-react'
import type { TaskStatus } from '@/api/types/enums'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { useTasksStore } from '@/stores/tasks'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { ROUTES } from '@/router/routes'
import { TaskCancelDialog } from './tasks/TaskCancelDialog'
import { TaskDeleteDialog } from './tasks/TaskDeleteDialog'
import { TaskDetailActions } from './tasks/TaskDetailActions'
import { TaskDetailHeader } from './tasks/TaskDetailHeader'
import { TaskDetailMetadata } from './tasks/TaskDetailMetadata'
import { TaskDetailTimeline } from './tasks/TaskDetailTimeline'
import { TaskTransitionDialog } from './tasks/TaskTransitionDialog'
import { requiresTransitionConfirmation } from './tasks/transition-confirmation'
import { useTaskActionHandlers } from './tasks/useTaskActionHandlers'
import { useTaskWebSocketUpdates } from './tasks/useTaskWebSocketUpdates'

export default function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const selectedTask = useTasksStore((s) => s.selectedTask)
  const loadingDetail = useTasksStore((s) => s.loadingDetail)
  const error = useTasksStore((s) => s.error)

  const [deleteOpen, setDeleteOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [pendingTransition, setPendingTransition] = useState<TaskStatus | null>(null)

  const { setupError: wsSetupError } = useTaskWebSocketUpdates()

  useEffect(() => {
    if (taskId) {
      void useTasksStore.getState().fetchTask(taskId)
    }
  }, [taskId])

  const task = selectedTask?.id === taskId ? selectedTask : undefined
  const { transitioning, transitionTo, deleteTask, cancelTask } = useTaskActionHandlers(task)

  // Walk the parent task list (already in store memory from the
  // board view). Empty on a deep link; the nav bar self-hides.
  const allTasks = useTasksStore((s) => s.tasks)
  const routeForTask = useCallback(
    (item: { id: string }) =>
      ROUTES.TASK_DETAIL.replace(':taskId', encodeURIComponent(item.id)),
    [],
  )
  const nav = useDetailNavigation({
    items: allTasks,
    currentId: taskId,
    routeFor: routeForTask,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  const handleTransitionRequest = useCallback(
    (target: TaskStatus) => {
      if (requiresTransitionConfirmation(target)) {
        setPendingTransition(target)
      } else {
        void transitionTo(target)
      }
    },
    [transitionTo],
  )

  const handleTransitionConfirm = useCallback(async () => {
    if (!pendingTransition) return
    const target = pendingTransition
    await transitionTo(target)
    setPendingTransition(null)
  }, [pendingTransition, transitionTo])

  if (error && !task) {
    return (
      <div className="mx-auto max-w-3xl space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Tasks', to: ROUTES.TASKS }, { label: taskId ?? 'Unknown task' }]} />
        <ErrorBanner severity="error" title="Could not load task" description={error} />
      </div>
    )
  }

  if (loadingDetail || !task) {
    return (
      <div
        className="flex items-center justify-center py-20"
        role="status"
        aria-label="Loading task"
      >
        <Loader2 className="size-8 animate-spin text-text-muted" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl space-y-section-gap">
      <div className="flex flex-wrap items-center gap-3">
        <Breadcrumbs items={[{ label: 'Tasks', to: ROUTES.TASKS }, { label: task.title ?? task.id }]} />
        <DetailNavBar
          canPrev={nav.canPrev}
          canNext={nav.canNext}
          onPrev={goPrev}
          onNext={goNext}
          position={nav.position}
        />
      </div>

      {wsSetupError && (
        <ErrorBanner
          variant="inline"
          severity="warning"
          title="Real-time updates unavailable"
          description={wsSetupError}
        />
      )}

      <ErrorBoundary level="section">
        <div className="rounded-lg border border-border bg-card p-card space-y-section-gap">
          <TaskDetailHeader task={task} />
          <TaskDetailMetadata task={task} />
          <TaskDetailTimeline task={task} />
          <TaskDetailActions
            task={task}
            transitioning={transitioning}
            onTransition={handleTransitionRequest}
            onRequestCancel={() => setCancelOpen(true)}
            onRequestDelete={() => setDeleteOpen(true)}
          />
        </div>
      </ErrorBoundary>

      <TaskCancelDialog open={cancelOpen} onOpenChange={setCancelOpen} onConfirm={cancelTask} />
      <TaskDeleteDialog open={deleteOpen} onOpenChange={setDeleteOpen} onConfirm={deleteTask} />
      <TaskTransitionDialog
        open={pendingTransition !== null}
        targetStatus={pendingTransition}
        transitioning={transitioning}
        onOpenChange={(next) => {
          if (!next) setPendingTransition(null)
        }}
        onConfirm={handleTransitionConfirm}
      />
    </div>
  )
}
