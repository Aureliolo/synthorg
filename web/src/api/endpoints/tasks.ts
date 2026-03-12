import { apiClient, unwrap, unwrapPaginated } from '../client'
import type {
  CancelTaskRequest,
  CreateTaskRequest,
  Task,
  TaskFilters,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '../types'

export async function listTasks(filters?: TaskFilters) {
  const response = await apiClient.get('/tasks', { params: filters })
  return unwrapPaginated<Task>(response)
}

export async function getTask(taskId: string): Promise<Task> {
  const response = await apiClient.get(`/tasks/${taskId}`)
  return unwrap(response)
}

export async function createTask(data: CreateTaskRequest): Promise<Task> {
  const response = await apiClient.post('/tasks', data)
  return unwrap(response)
}

export async function updateTask(taskId: string, data: UpdateTaskRequest): Promise<Task> {
  const response = await apiClient.patch(`/tasks/${taskId}`, data)
  return unwrap(response)
}

export async function transitionTask(taskId: string, data: TransitionTaskRequest): Promise<Task> {
  const response = await apiClient.post(`/tasks/${taskId}/transition`, data)
  return unwrap(response)
}

export async function cancelTask(taskId: string, data: CancelTaskRequest): Promise<Task> {
  const response = await apiClient.post(`/tasks/${taskId}/cancel`, data)
  return unwrap(response)
}

export async function deleteTask(taskId: string): Promise<void> {
  await apiClient.delete(`/tasks/${taskId}`)
}
