import { useCallback, useEffect, useMemo } from 'react'
import { useTasksStore } from '@/stores/tasks'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type { TaskStatus } from '@/api/types/enums'
import type {
  CancelTaskRequest,
  CreateTaskRequest,
  Task,
  TaskBoardSubmissionResponse,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '@/api/types/tasks'
import type { WsChannel } from '@/api/types/websocket'

const TASK_POLL_INTERVAL = 30_000
const TASK_CHANNELS = ['tasks'] as const satisfies readonly WsChannel[]

export interface UseTaskBoardDataReturn {
  tasks: Task[]
  selectedTask: Task | null
  total: number
  loading: boolean
  loadingDetail: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
  fetchTask: (taskId: string) => Promise<void>
  // Mirror the canonical store contract: failure resolves to `null` /
  // `false`. Callers must null-check the sentinel rather than try/catch.
  createTask: (data: CreateTaskRequest) => Promise<TaskBoardSubmissionResponse | null>
  updateTask: (taskId: string, data: UpdateTaskRequest) => Promise<Task | null>
  transitionTask: (taskId: string, data: TransitionTaskRequest) => Promise<Task | null>
  cancelTask: (taskId: string, data: CancelTaskRequest) => Promise<Task | null>
  deleteTask: (taskId: string) => Promise<boolean>
  optimisticTransition: (taskId: string, targetStatus: TaskStatus) => () => void
}

export function useTaskBoardData(): UseTaskBoardDataReturn {
  const tasks = useTasksStore((s) => s.tasks)
  const selectedTask = useTasksStore((s) => s.selectedTask)
  const total = useTasksStore((s) => s.total)
  const loading = useTasksStore((s) => s.loading)
  const loadingDetail = useTasksStore((s) => s.loadingDetail)
  const error = useTasksStore((s) => s.error)
  const fetchTask = useTasksStore((s) => s.fetchTask)
  const createTask = useTasksStore((s) => s.createTask)
  const updateTask = useTasksStore((s) => s.updateTask)
  const transitionTask = useTasksStore((s) => s.transitionTask)
  const cancelTask = useTasksStore((s) => s.cancelTask)
  const deleteTask = useTasksStore((s) => s.deleteTask)
  const optimisticTransition = useTasksStore((s) => s.optimisticTransition)

  // Initial data fetch -- always fetches all tasks; filtering is client-side
  useEffect(() => {
    void useTasksStore.getState().fetchTasks({ limit: 200 })
  }, [])

  // Lightweight polling for task refresh
  const pollFn = useCallback(async () => {
    await useTasksStore.getState().fetchTasks({ limit: 200 })
  }, [])

  const polling = usePolling(pollFn, TASK_POLL_INTERVAL)

  useEffect(() => {
    polling.start()
    return () => polling.stop()
  }, []) // eslint-disable-line @eslint-react/exhaustive-deps

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      TASK_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useTasksStore.getState().handleWsEvent(event)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    tasks,
    selectedTask,
    total,
    loading,
    loadingDetail,
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
  }
}
