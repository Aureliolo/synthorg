import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { ROUTES } from '@/router/routes'
import { useTasksStore } from '@/stores/tasks'
import { useToastStore } from '@/stores/toast'
import type { TaskStatus } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'

export interface TaskActionHandlers {
  transitioning: TaskStatus | null
  transitionTo: (targetStatus: TaskStatus) => Promise<void>
  deleteTask: () => Promise<void>
  cancelTask: (reason: string) => Promise<boolean>
}

/**
 * Page-level orchestration around the canonical task store mutations.
 *
 * The store owns the success/error toast UX -- this hook only adds
 * page-specific concerns (UI spinner state, navigation on delete,
 * input validation for cancellation reason). It deliberately does NOT
 * wrap the store mutations in try/catch; instead it null-checks the
 * sentinel returns to decide whether to navigate / unwind.
 */
export function useTaskActionHandlers(task: Task | null | undefined): TaskActionHandlers {
  const navigate = useNavigate()
  const [transitioning, setTransitioning] = useState<TaskStatus | null>(null)

  const transitionTo = useCallback(
    async (targetStatus: TaskStatus) => {
      if (!task) return
      setTransitioning(targetStatus)
      try {
        // Sentinel (null) return is intentionally discarded: the store
        // already emits the error toast and the board row re-subscribes
        // to the WS event that would restore the old status if the
        // transition failed, so there's nothing page-level to unwind.
        await useTasksStore.getState().transitionTask(task.id, {
          target_status: targetStatus,
          expected_version: task.version,
        })
      } finally {
        setTransitioning(null)
      }
    },
    [task],
  )

  const deleteTask = useCallback(async () => {
    if (!task) return
    const ok = await useTasksStore.getState().deleteTask(task.id)
    if (ok) {
      navigate(ROUTES.TASKS)
    }
  }, [task, navigate])

  const cancelTask = useCallback(
    async (reason: string) => {
      if (!task) return false
      const trimmed = reason.trim()
      if (!trimmed) {
        useToastStore.getState().add({
          variant: 'error',
          title: 'Cancellation reason required',
          description:
            'Cancellation requires a reason for audit. Please enter why you are cancelling this task.',
        })
        return false
      }
      const result = await useTasksStore
        .getState()
        .cancelTask(task.id, { reason: trimmed })
      return result !== null
    },
    [task],
  )

  return { transitioning, transitionTo, deleteTask, cancelTask }
}
