import { apiClient, unwrap, unwrapPaginated, withSignal, type PaginatedResult } from '../client'
import type {
  DocSearchHit,
  DocSummary,
  DocType,
  DocVersion,
  LivingDocument,
} from '../types'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export interface ListProjectDocsParams {
  cursor?: string | null
  limit?: number
  doc_type?: DocType
  tag?: string
}

export async function listProjectDocs(
  projectId: string,
  params?: ListProjectDocsParams,
  signal?: AbortSignal,
): Promise<PaginatedResult<DocSummary>> {
  const response = await apiClient.get<PaginatedResponse<DocSummary>>(
    `/projects/${encodeURIComponent(projectId)}/docs`,
    withSignal(signal, { params }),
  )
  return unwrapPaginated<DocSummary>(response)
}

export async function getProjectDoc(
  projectId: string,
  slug: string,
  signal?: AbortSignal,
): Promise<LivingDocument> {
  const response = await apiClient.get<ApiResponse<LivingDocument>>(
    `/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(slug)}`,
    withSignal(signal),
  )
  return unwrap(response)
}

export async function getProjectDocHistory(
  projectId: string,
  slug: string,
): Promise<readonly DocVersion[]> {
  const response = await apiClient.get<ApiResponse<readonly DocVersion[]>>(
    `/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(slug)}/history`,
  )
  return unwrap(response)
}

export async function searchProjectDocs(
  projectId: string,
  query: string,
  limit = 8,
): Promise<readonly DocSearchHit[]> {
  const response = await apiClient.get<ApiResponse<readonly DocSearchHit[]>>(
    `/projects/${encodeURIComponent(projectId)}/docs/search`,
    { params: { q: query, limit } },
  )
  return unwrap(response)
}
