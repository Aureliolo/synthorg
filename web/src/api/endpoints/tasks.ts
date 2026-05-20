import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  CancelTaskRequest,
  CreateTaskRequest,
  Task,
  TaskBoardSubmissionResponse,
  TaskFilters,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '../types/tasks'

export async function listTasks(filters?: TaskFilters): Promise<PaginatedResult<Task>> {
  const response = await apiClient.get<PaginatedResponse<Task>>('/tasks', { params: filters })
  return unwrapPaginated<Task>(response)
}

export async function getTask(taskId: string): Promise<Task> {
  const response = await apiClient.get<ApiResponse<Task>>(`/tasks/${encodeURIComponent(taskId)}`)
  return unwrap(response)
}

/**
 * File a new task into the live work pipeline.
 *
 * Returns the 202 ``TaskBoardSubmissionResponse`` envelope, NOT a
 * ``Task``: the spine creates the task in its background intake phase
 * and the board UI receives it via the ``tasks`` WebSocket channel's
 * ``task.created`` event, correlated by ``correlation_id``.
 */
export async function createTask(data: CreateTaskRequest): Promise<TaskBoardSubmissionResponse> {
  const response = await apiClient.post<ApiResponse<TaskBoardSubmissionResponse>>('/tasks', data)
  return unwrap(response)
}

export async function updateTask(taskId: string, data: UpdateTaskRequest): Promise<Task> {
  const response = await apiClient.patch<ApiResponse<Task>>(`/tasks/${encodeURIComponent(taskId)}`, data)
  return unwrap(response)
}

export async function transitionTask(taskId: string, data: TransitionTaskRequest): Promise<Task> {
  const response = await apiClient.post<ApiResponse<Task>>(`/tasks/${encodeURIComponent(taskId)}/transition`, data)
  return unwrap(response)
}

export async function cancelTask(taskId: string, data: CancelTaskRequest): Promise<Task> {
  const response = await apiClient.post<ApiResponse<Task>>(`/tasks/${encodeURIComponent(taskId)}/cancel`, data)
  return unwrap(response)
}

export async function deleteTask(taskId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(`/tasks/${encodeURIComponent(taskId)}`)
  unwrapVoid(response)
}
