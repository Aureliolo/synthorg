import { Loader2, Workflow } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import type { TaskStatus } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import { ROUTES } from '@/router/routes'
import { getAvailableTransitions, getTaskStatusLabel } from '@/utils/tasks'

interface TaskDetailActionsProps {
  task: Task
  transitioning: TaskStatus | null
  onTransition: (targetStatus: TaskStatus) => void
  onRequestCancel: () => void
  onRequestDelete: () => void
}

export function TaskDetailActions(props: TaskDetailActionsProps) {
  const { task, transitioning, onTransition, onRequestCancel, onRequestDelete } = props
  const availableTransitions = getAvailableTransitions(task.status)

  return (
    <>
      {availableTransitions.length > 0 && (
        <div>
          <span className="text-compact font-semibold uppercase tracking-wider text-text-muted">
            Transitions
          </span>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {availableTransitions.map((targetStatus) => (
              <Button
                key={targetStatus}
                size="sm"
                variant="outline"
                disabled={transitioning !== null}
                onClick={() => onTransition(targetStatus)}
              >
                {transitioning === targetStatus && (
                  <Loader2 className="mr-1 size-3 animate-spin" />
                )}
                {getTaskStatusLabel(targetStatus)}
              </Button>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 border-t border-border pt-4">
        <Button variant="outline" size="sm" asChild>
          <Link to={ROUTES.TASK_DECOMPOSE.replace(':taskId', task.id)}>
            <Workflow />
            Decompose
          </Link>
        </Button>
        {task.status !== 'cancelled' && task.status !== 'completed' && (
          <Button variant="outline" size="sm" onClick={onRequestCancel}>
            Cancel Task
          </Button>
        )}
        <Button variant="destructive" size="sm" onClick={onRequestDelete}>
          Delete
        </Button>
      </div>
    </>
  )
}
