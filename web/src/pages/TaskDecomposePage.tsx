import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ROUTES } from '@/router/routes'
import { TaskDecomposeForm } from './tasks/TaskDecomposeForm'
import { TaskDecomposeResult } from './tasks/TaskDecomposeResult'
import { useTaskDecomposeController } from './tasks/useTaskDecomposeController'

export default function TaskDecomposePage() {
  const { taskId } = useParams<{ taskId: string }>()
  const ctrl = useTaskDecomposeController(taskId)

  return (
    <div className="mx-auto max-w-3xl space-y-section-gap">
      <Breadcrumbs
        items={[
          { label: 'Tasks', to: ROUTES.TASKS },
          {
            label: taskId ?? 'Task',
            to: ROUTES.TASK_DETAIL.replace(':taskId', taskId ?? ''),
          },
          { label: 'Decompose' },
        ]}
      />
      <ErrorBoundary level="section">
        <TaskDecomposeForm
          drafts={ctrl.drafts}
          submitting={ctrl.submitting}
          onChange={ctrl.updateDraft}
          onRemove={ctrl.removeDraft}
          onAdd={ctrl.addDraft}
          onSubmit={() => {
            void ctrl.submit()
          }}
        />
        {ctrl.result && <TaskDecomposeResult result={ctrl.result} />}
      </ErrorBoundary>
    </div>
  )
}
