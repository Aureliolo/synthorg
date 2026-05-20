import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export type DocType = 'status_report' | 'deliverable' | 'knowledge_note'

export interface DocSummary {
  project_id: string
  slug: string
  title: string
  doc_type: DocType
  tags: readonly string[]
  updated_at: string
}

export interface DocBlock {
  block_kind: string
  block_id: string
  [key: string]: unknown
}

export interface LivingDocument {
  slug: string
  title: string
  doc_type: DocType
  tags: readonly string[]
  related_task_ids: readonly string[]
  author_agent_id: string
  body: readonly DocBlock[]
  created_at: string
  updated_at: string
}

export interface DocVersion {
  commit_sha: string
  author_agent_id: string
  committed_at: string
  summary: string
}

export interface DocSearchHit {
  project_id: string
  doc_slug: string
  doc_type: DocType
  chunk_text: string
  relevance_score: number
}

export interface ListProjectDocsParams {
  cursor?: string | null
  limit?: number
  doc_type?: DocType
  tag?: string
}

export async function listProjectDocs(
  projectId: string,
  params?: ListProjectDocsParams,
): Promise<PaginatedResult<DocSummary>> {
  const response = await apiClient.get<PaginatedResponse<DocSummary>>(
    `/projects/${encodeURIComponent(projectId)}/docs`,
    { params },
  )
  return unwrapPaginated<DocSummary>(response)
}

export async function getProjectDoc(
  projectId: string,
  slug: string,
): Promise<LivingDocument> {
  const response = await apiClient.get<ApiResponse<LivingDocument>>(
    `/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(slug)}`,
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
