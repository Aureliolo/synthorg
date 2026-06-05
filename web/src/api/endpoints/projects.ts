import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type { CreateProjectRequest, Project, ProjectFilters } from '../types/projects'

export async function listProjects(filters?: ProjectFilters): Promise<PaginatedResult<Project>> {
  const response = await apiClient.get<PaginatedResponse<Project>>('/projects', { params: filters })
  return unwrapPaginated<Project>(response)
}

export async function getProject(projectId: string): Promise<Project> {
  const response = await apiClient.get<ApiResponse<Project>>(`/projects/${encodeURIComponent(projectId)}`)
  return unwrap(response)
}

export async function createProject(data: CreateProjectRequest): Promise<Project> {
  const response = await apiClient.post<ApiResponse<Project>>('/projects', data)
  return unwrap(response)
}

export async function deleteProject(projectId: string): Promise<void> {
  await apiClient.delete(`/projects/${encodeURIComponent(projectId)}`)
}
