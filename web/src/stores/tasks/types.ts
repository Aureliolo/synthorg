import type { StoreApi } from 'zustand'
import type { TaskStatus } from '@/api/types/enums'
import type {
  CancelTaskRequest,
  CreateTaskRequest,
  DashboardTask,
  TaskBoardSubmissionResponse,
  TaskFilters,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'

export interface TasksState {
  // Data
  tasks: DashboardTask[]
  selectedTask: DashboardTask | null
  total: number

  // Loading states
  loading: boolean
  loadingDetail: boolean
  error: string | null

  // Actions. Mutations follow the canonical store error contract: on
  // failure they log + emit an error toast + return a sentinel
  // (`null` for entity-returning ops, `false` for delete). Callers MUST
  // NOT wrap these in try/catch; check the sentinel and branch on it.
  fetchTasks: (filters?: TaskFilters) => Promise<void>
  fetchTask: (taskId: string) => Promise<void>
  createTask: (
    data: CreateTaskRequest,
  ) => Promise<TaskBoardSubmissionResponse | null>
  updateTask: (
    taskId: string,
    data: UpdateTaskRequest,
  ) => Promise<DashboardTask | null>
  transitionTask: (
    taskId: string,
    data: TransitionTaskRequest,
  ) => Promise<DashboardTask | null>
  cancelTask: (
    taskId: string,
    data: CancelTaskRequest,
  ) => Promise<DashboardTask | null>
  deleteTask: (taskId: string) => Promise<boolean>

  // Real-time
  handleWsEvent: (event: WsEvent) => void

  // Optimistic helpers
  pendingTransitions: Set<string>
  optimisticTransition: (taskId: string, targetStatus: TaskStatus) => () => void
  upsertTask: (task: DashboardTask) => void
  removeTask: (taskId: string) => void
}

export type TasksSet = StoreApi<TasksState>['setState']
export type TasksGet = StoreApi<TasksState>['getState']
