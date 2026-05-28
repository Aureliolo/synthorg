import { useParams } from 'react-router'
import { Loader2 } from 'lucide-react'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ROUTES } from '@/router/routes'
import { TaskCancelDialog } from './tasks/TaskCancelDialog'
import { TaskDeleteDialog } from './tasks/TaskDeleteDialog'
import { TaskDetailActions } from './tasks/TaskDetailActions'
import { TaskDetailHeader } from './tasks/TaskDetailHeader'
import { TaskDetailMetadata } from './tasks/TaskDetailMetadata'
import { TaskDetailTimeline } from './tasks/TaskDetailTimeline'
import { TaskTransitionDialog } from './tasks/TaskTransitionDialog'
import { useTaskDetailController } from './tasks/useTaskDetailController'

export default function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const ctrl = useTaskDetailController(taskId)

  if (ctrl.error && !ctrl.task) {
    return (
      <div className="mx-auto max-w-3xl space-y-section-gap">
        <Breadcrumbs
          items={[
            { label: 'Tasks', to: ROUTES.TASKS },
            { label: taskId ?? 'Unknown task' },
          ]}
        />
        <ErrorBanner severity="error" title="Could not load task" description={ctrl.error} />
      </div>
    )
  }

  if (ctrl.loadingDetail || !ctrl.task) return <TaskDetailLoading />

  const task = ctrl.task

  return (
    <div className="mx-auto max-w-3xl space-y-section-gap">
      <TaskDetailBreadcrumbsRow task={task} ctrl={ctrl} />
      {ctrl.wsSetupError && (
        <ErrorBanner
          variant="inline"
          severity="warning"
          title="Real-time updates unavailable"
          description={ctrl.wsSetupError}
        />
      )}
      <ErrorBoundary level="section">
        <div className="rounded-lg border border-border bg-card p-card space-y-section-gap">
          <TaskDetailHeader task={task} />
          <TaskDetailMetadata task={task} />
          <TaskDetailTimeline task={task} />
          <TaskDetailActions
            task={task}
            transitioning={ctrl.transitioning}
            onTransition={ctrl.handleTransitionRequest}
            onRequestCancel={() => ctrl.setCancelOpen(true)}
            onRequestDelete={() => ctrl.setDeleteOpen(true)}
          />
        </div>
      </ErrorBoundary>
      <TaskDetailDialogs ctrl={ctrl} />
    </div>
  )
}

function TaskDetailLoading() {
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

interface TaskDetailBreadcrumbsRowProps {
  task: NonNullable<ReturnType<typeof useTaskDetailController>['task']>
  ctrl: ReturnType<typeof useTaskDetailController>
}

function TaskDetailBreadcrumbsRow({ task, ctrl }: TaskDetailBreadcrumbsRowProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Breadcrumbs
        items={[
          { label: 'Tasks', to: ROUTES.TASKS },
          { label: task.title ?? task.id },
        ]}
      />
      <DetailNavBar
        canPrev={ctrl.nav.canPrev}
        canNext={ctrl.nav.canNext}
        onPrev={ctrl.goPrev}
        onNext={ctrl.goNext}
        position={ctrl.nav.position}
      />
    </div>
  )
}

interface TaskDetailDialogsProps {
  ctrl: ReturnType<typeof useTaskDetailController>
}

function TaskDetailDialogs({ ctrl }: TaskDetailDialogsProps) {
  return (
    <>
      <TaskCancelDialog
        open={ctrl.cancelOpen}
        onOpenChange={ctrl.setCancelOpen}
        onConfirm={ctrl.cancelTask}
      />
      <TaskDeleteDialog
        open={ctrl.deleteOpen}
        onOpenChange={ctrl.setDeleteOpen}
        onConfirm={ctrl.deleteTask}
      />
      <TaskTransitionDialog
        open={ctrl.pendingTransition !== null}
        targetStatus={ctrl.pendingTransition}
        transitioning={ctrl.transitioning}
        onOpenChange={(next) => {
          if (!next) ctrl.setPendingTransition(null)
        }}
        onConfirm={ctrl.handleTransitionConfirm}
      />
    </>
  )
}
