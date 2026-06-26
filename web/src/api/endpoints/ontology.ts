/**
 * Ontology API endpoints -- entity CRUD, versioning, drift.
 */
import type { AxiosResponse } from 'axios'
import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  CreateEntityRequest,
  DriftAgentResponse,
  DriftReportResponse,
  EntityFieldResponse,
  EntityListMeta,
  EntityListResponse,
  EntityRelationResponse,
  EntityResponse,
  EntityVersionResponse,
  UpdateEntityRequest,
} from '../types'

// ── Types ─────────────────────────────────────────────────────

export type {
  CreateEntityRequest,
  DriftAgentResponse,
  DriftReportResponse,
  EntityFieldResponse,
  EntityListMeta,
  EntityRelationResponse,
  EntityResponse,
  EntityVersionResponse,
  UpdateEntityRequest,
}

/**
 * Paginated entity page plus the backend's catalog-wide aggregates
 * (``core_count`` / ``user_count`` / ``total_count`` / ``drift_summary``)
 * carried on ``EntityListResponse.meta`` -- distinct from the per-page
 * ``data.length`` so the UI can show real totals without a second call.
 */
export interface EntityListResult extends PaginatedResult<EntityResponse> {
  readonly meta: EntityListMeta
}

// ── Endpoints ─────────────────────────────────────────────────

export async function listEntities(params?: {
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  cursor?: string | null
  limit?: number
  tier?: string
}): Promise<EntityListResult> {
  const response = await apiClient.get<EntityListResponse>('/ontology/entities', {
    params,
  })
  // ``unwrapPaginated`` only reads ``data`` / ``pagination`` and never
  // mutates, so the wire envelope's ``readonly`` data is safe to pass.
  const page = unwrapPaginated<EntityResponse>(
    response as unknown as AxiosResponse<PaginatedResponse<EntityResponse>>,
  )
  return { ...page, meta: response.data.meta }
}

export async function getEntity(name: string): Promise<EntityResponse> {
  const response = await apiClient.get<ApiResponse<EntityResponse>>(
    `/ontology/entities/${encodeURIComponent(name)}`,
  )
  return unwrap(response)
}

export async function createEntity(data: CreateEntityRequest): Promise<EntityResponse> {
  const response = await apiClient.post<ApiResponse<EntityResponse>>('/ontology/entities', data)
  return unwrap(response)
}

export async function updateEntity(
  name: string,
  data: UpdateEntityRequest,
): Promise<EntityResponse> {
  const response = await apiClient.put<ApiResponse<EntityResponse>>(
    `/ontology/entities/${encodeURIComponent(name)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteEntity(name: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/ontology/entities/${encodeURIComponent(name)}`,
  )
  unwrapVoid(response)
}

export async function listEntityVersions(
  name: string,
  params?: { cursor?: string | null; limit?: number },
): Promise<PaginatedResult<EntityVersionResponse>> {
  const response = await apiClient.get<PaginatedResponse<EntityVersionResponse>>(
    `/ontology/entities/${encodeURIComponent(name)}/versions`,
    { params },
  )
  return unwrapPaginated<EntityVersionResponse>(response)
}

export async function getVersionManifest(): Promise<Record<string, number>> {
  const response = await apiClient.get<ApiResponse<Record<string, number>>>('/ontology/manifest')
  return unwrap(response)
}

export async function listDriftReports(params?: {
  cursor?: string | null
  limit?: number
}): Promise<PaginatedResult<DriftReportResponse>> {
  const response = await apiClient.get<PaginatedResponse<DriftReportResponse>>('/ontology/drift', {
    params,
  })
  return unwrapPaginated<DriftReportResponse>(response)
}

export async function triggerDriftCheck(): Promise<Record<string, string>> {
  const response = await apiClient.post<ApiResponse<Record<string, string>>>(
    '/ontology/drift/check',
  )
  return unwrap(response)
}

// ── Admin operations ──────────────────────────────────────────

export interface DeriveOntologyResponse {
  derived_count: number
}

export type SyncOrgMemoryStatus = 'sync_completed' | 'sync_service_not_configured'

export interface SyncOrgMemoryResponse {
  status: SyncOrgMemoryStatus
  /**
   * Count of definitions published to OrgMemory. Present only on a
   * completed sync; the backend omits it on the
   * ``sync_service_not_configured`` branch.
   */
  published_count?: number
}

/** Re-run auto-derivation of entity definitions from decorated models. */
export async function deriveOntology(): Promise<DeriveOntologyResponse> {
  const response = await apiClient.post<ApiResponse<DeriveOntologyResponse>>(
    '/ontology/admin/derive',
  )
  return unwrap(response)
}

/** Force a re-sync of all entity definitions into OrgMemory. */
export async function syncOrgMemory(): Promise<SyncOrgMemoryResponse> {
  const response = await apiClient.post<ApiResponse<SyncOrgMemoryResponse>>(
    '/ontology/admin/sync-org-memory',
  )
  return unwrap(response)
}
