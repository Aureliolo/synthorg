import { apiClient, unwrap, unwrapPaginated, withSignal, type PaginatedResult } from '../client'
import type {
  BrainEntry,
  BrainEntryKind,
  BrainEntryStatus,
  BrainEntryVersion,
  BrainSearchHit,
  BrainSummary,
} from '../types'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export interface ListProjectBrainParams {
  cursor?: string | null
  limit?: number
  entry_kind?: BrainEntryKind
  status?: BrainEntryStatus
}

export async function listProjectBrain(
  projectId: string,
  params?: ListProjectBrainParams,
  signal?: AbortSignal,
): Promise<PaginatedResult<BrainSummary>> {
  const response = await apiClient.get<PaginatedResponse<BrainSummary>>(
    `/projects/${encodeURIComponent(projectId)}/brain`,
    withSignal(signal, { params }),
  )
  return unwrapPaginated<BrainSummary>(response)
}

export async function getProjectBrainEntry(
  projectId: string,
  entryId: string,
  signal?: AbortSignal,
): Promise<BrainEntry> {
  const response = await apiClient.get<ApiResponse<BrainEntry>>(
    `/projects/${encodeURIComponent(projectId)}/brain/${encodeURIComponent(entryId)}`,
    withSignal(signal),
  )
  return unwrap(response)
}

export async function getProjectBrainHistory(
  projectId: string,
  entryId: string,
  signal?: AbortSignal,
): Promise<readonly BrainEntryVersion[]> {
  const response = await apiClient.get<ApiResponse<readonly BrainEntryVersion[]>>(
    `/projects/${encodeURIComponent(projectId)}/brain/${encodeURIComponent(entryId)}/history`,
    withSignal(signal),
  )
  return unwrap(response)
}

export async function searchProjectBrain(
  projectId: string,
  query: string,
  limit = 8,
  signal?: AbortSignal,
): Promise<readonly BrainSearchHit[]> {
  const response = await apiClient.get<ApiResponse<readonly BrainSearchHit[]>>(
    `/projects/${encodeURIComponent(projectId)}/brain/search`,
    withSignal(signal, { params: { q: query, limit } }),
  )
  return unwrap(response)
}
