import { apiClient, unwrap, unwrapPaginated } from '../client'
import type {
  ApiResponse,
  CancelTaskRequest,
  CreateTaskRequest,
  PaginatedResponse,
  Task,
  TaskFilters,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '../types'

export async function listTasks(filters?: TaskFilters) {
  const response = await apiClient.get<PaginatedResponse<Task>>('/tasks', { params: filters })
  return unwrapPaginated<Task>(response)
}

export async function getTask(taskId: string): Promise<Task> {
  const response = await apiClient.get<ApiResponse<Task>>(`/tasks/${encodeURIComponent(taskId)}`)
  return unwrap(response)
}

export async function createTask(data: CreateTaskRequest): Promise<Task> {
  const response = await apiClient.post<ApiResponse<Task>>('/tasks', data)
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
  await apiClient.delete(`/tasks/${encodeURIComponent(taskId)}`)
}
