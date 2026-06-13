import { apiClient, unwrap, unwrapPaginated, withSignal, type PaginatedResult } from '../client'
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
    withSignal(signal, { params }),
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
    withSignal(signal),
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
    withSignal(signal, { params: { q: query, limit } }),
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
    withSignal(signal, { params }),
  )
  return unwrapPaginated<KnowledgeSource>(response)
}
