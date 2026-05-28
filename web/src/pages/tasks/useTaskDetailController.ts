import { useCallback, useEffect, useState } from 'react'

import type { TaskStatus } from '@/api/types/enums'
import type { DashboardTask } from '@/api/types/tasks'
import { useTasksStore } from '@/stores/tasks'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { ROUTES } from '@/router/routes'

import { requiresTransitionConfirmation } from './transition-confirmation'
import { useTaskActionHandlers } from './useTaskActionHandlers'
import { useTaskWebSocketUpdates } from './useTaskWebSocketUpdates'

export interface TaskDetailController {
  task: DashboardTask | undefined
  loadingDetail: boolean
  error: string | null
  wsSetupError: string | null
  deleteOpen: boolean
  cancelOpen: boolean
  pendingTransition: TaskStatus | null
  transitioning: ReturnType<typeof useTaskActionHandlers>['transitioning']
  nav: ReturnType<typeof useDetailNavigation>
  goPrev: () => void
  goNext: () => void
  deleteTask: ReturnType<typeof useTaskActionHandlers>['deleteTask']
  cancelTask: ReturnType<typeof useTaskActionHandlers>['cancelTask']
  setDeleteOpen: (open: boolean) => void
  setCancelOpen: (open: boolean) => void
  setPendingTransition: (status: TaskStatus | null) => void
  handleTransitionRequest: (target: TaskStatus) => void
  handleTransitionConfirm: () => Promise<void>
}

export function useTaskDetailController(taskId: string | undefined): TaskDetailController {
  const selectedTask = useTasksStore((s) => s.selectedTask)
  const loadingDetail = useTasksStore((s) => s.loadingDetail)
  const error = useTasksStore((s) => s.error)
  const allTasks = useTasksStore((s) => s.tasks)

  const [deleteOpen, setDeleteOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [pendingTransition, setPendingTransition] = useState<TaskStatus | null>(null)

  const { setupError: wsSetupError } = useTaskWebSocketUpdates()

  useEffect(() => {
    if (taskId) void useTasksStore.getState().fetchTask(taskId)
  }, [taskId])

  const task = selectedTask && selectedTask.id === taskId ? selectedTask : undefined
  const { transitioning, transitionTo, deleteTask, cancelTask } = useTaskActionHandlers(task)

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
      if (requiresTransitionConfirmation(target)) setPendingTransition(target)
      else void transitionTo(target)
    },
    [transitionTo],
  )

  const handleTransitionConfirm = useCallback(async () => {
    if (!pendingTransition) return
    const target = pendingTransition
    await transitionTo(target)
    setPendingTransition(null)
  }, [pendingTransition, transitionTo])

  return {
    task,
    loadingDetail,
    error,
    wsSetupError,
    deleteOpen,
    cancelOpen,
    pendingTransition,
    transitioning,
    nav,
    goPrev,
    goNext,
    deleteTask,
    cancelTask,
    setDeleteOpen,
    setCancelOpen,
    setPendingTransition,
    handleTransitionRequest,
    handleTransitionConfirm,
  }
}
