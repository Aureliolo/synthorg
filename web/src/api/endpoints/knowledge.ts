import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { KnowledgeHit, KnowledgeSource } from '../types'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export interface ListProjectKnowledgeParams {
  cursor?: string | null
  limit?: number
  include_global?: boolean
  stale_only?: boolean
}

export async function listProjectKnowledgeSources(
  projectId: string,
  params?: ListProjectKnowledgeParams,
  signal?: AbortSignal,
): Promise<PaginatedResult<KnowledgeSource>> {
  const response = await apiClient.get<PaginatedResponse<KnowledgeSource>>(
    `/projects/${encodeURIComponent(projectId)}/knowledge`,
    { params, signal },
  )
  return unwrapPaginated<KnowledgeSource>(response)
}

export async function getProjectKnowledgeSource(
  projectId: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<KnowledgeSource> {
  const response = await apiClient.get<ApiResponse<KnowledgeSource>>(
    `/projects/${encodeURIComponent(projectId)}/knowledge/${encodeURIComponent(sourceId)}`,
    { signal },
  )
  return unwrap(response)
}

export async function searchProjectKnowledge(
  projectId: string,
  query: string,
  limit = 8,
  signal?: AbortSignal,
): Promise<readonly KnowledgeHit[]> {
  const response = await apiClient.get<ApiResponse<readonly KnowledgeHit[]>>(
    `/projects/${encodeURIComponent(projectId)}/knowledge/search`,
    { params: { q: query, limit }, signal },
  )
  return unwrap(response)
}

export interface ListGlobalKnowledgeParams {
  cursor?: string | null
  limit?: number
  stale_only?: boolean
}

export async function listGlobalKnowledgeSources(
  params?: ListGlobalKnowledgeParams,
  signal?: AbortSignal,
): Promise<PaginatedResult<KnowledgeSource>> {
  const response = await apiClient.get<PaginatedResponse<KnowledgeSource>>(
    '/knowledge',
    { params, signal },
  )
  return unwrapPaginated<KnowledgeSource>(response)
}
