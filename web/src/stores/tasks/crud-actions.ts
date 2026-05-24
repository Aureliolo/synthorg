import * as tasksApi from '@/api/endpoints/tasks'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  CancelTaskRequest,
  CreateTaskRequest,
  DashboardTask,
  TaskBoardSubmissionResponse,
  TaskFilters,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '@/api/types/tasks'
import type { TasksGet, TasksSet } from './types'

const log = createLogger('tasks')

async function fetchTasksImpl(
  set: TasksSet,
  filters?: TaskFilters,
): Promise<void> {
  set({ loading: true, error: null })
  try {
    const result = await tasksApi.listTasks(filters)
    set({
      tasks: result.data,
      total: result.data.length,
      loading: false,
    })
  } catch (err) {
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchTaskImpl(set: TasksSet, taskId: string): Promise<void> {
  set({ loadingDetail: true })
  try {
    const task = await tasksApi.getTask(taskId)
    set({ selectedTask: task, loadingDetail: false })
  } catch (err) {
    set({ loadingDetail: false, error: getErrorMessage(err) })
  }
}

async function createTaskImpl(
  data: CreateTaskRequest,
): Promise<TaskBoardSubmissionResponse | null> {
  try {
    const submission = await tasksApi.createTask(data)
    // The spine creates the task in its background intake phase; the
    // board UI inserts the real task on the matching ``task.created``
    // WS event. The 202 envelope carries only correlation metadata,
    // not a ``Task``, so we do NOT mutate ``tasks``/``total`` here.
    useToastStore.getState().add({
      variant: 'success',
      title: `Task ${submission.title} submitted`,
      description:
        'The work pipeline is starting; the card will appear shortly.',
    })
    return submission
  } catch (err) {
    log.error('Submit task failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to submit task'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function mutateTaskImpl(
  get: TasksGet,
  call: () => Promise<DashboardTask>,
  successTitle: (task: DashboardTask) => string,
  errorTitle: string,
  logPrefix: string,
): Promise<DashboardTask | null> {
  try {
    const task = await call()
    get().upsertTask(task)
    useToastStore.getState().add({
      variant: 'success',
      title: successTitle(task),
    })
    return task
  } catch (err) {
    log.error(`${logPrefix} failed:`, sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, errorTitle),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function deleteTaskImpl(
  set: TasksSet,
  get: TasksGet,
  taskId: string,
): Promise<boolean> {
  try {
    await tasksApi.deleteTask(taskId)
    get().removeTask(taskId)
    // Clear the dangling selection so a detail drawer doesn't
    // keep showing a task the store has already removed.
    if (get().selectedTask?.id === taskId) {
      set({ selectedTask: null })
    }
    useToastStore.getState().add({
      variant: 'success',
      title: 'Task deleted',
    })
    return true
  } catch (err) {
    log.error('Delete task failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to delete task'),
      description: getErrorMessage(err),
    })
    return false
  }
}

export function createCrudActions(set: TasksSet, get: TasksGet) {
  return {
    fetchTasks: (filters?: TaskFilters) => fetchTasksImpl(set, filters),
    fetchTask: (taskId: string) => fetchTaskImpl(set, taskId),
    createTask: (data: CreateTaskRequest) => createTaskImpl(data),
    updateTask: (taskId: string, data: UpdateTaskRequest) =>
      mutateTaskImpl(
        get,
        () => tasksApi.updateTask(taskId, data),
        (task) => `Task ${task.title} updated`,
        'Failed to update task',
        'Update task',
      ),
    transitionTask: (taskId: string, data: TransitionTaskRequest) =>
      mutateTaskImpl(
        get,
        () => tasksApi.transitionTask(taskId, data),
        (task) => `Task ${task.title} -> ${task.status}`,
        'Failed to transition task',
        'Transition task',
      ),
    cancelTask: (taskId: string, data: CancelTaskRequest) =>
      mutateTaskImpl(
        get,
        () => tasksApi.cancelTask(taskId, data),
        (task) => `Task ${task.title} cancelled`,
        'Failed to cancel task',
        'Cancel task',
      ),
    deleteTask: (taskId: string) => deleteTaskImpl(set, get, taskId),
  }
}
