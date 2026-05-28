/**
 * Ontology API endpoints -- entity CRUD, versioning, drift.
 */
import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

// ── Types ─────────────────────────────────────────────────────

export interface EntityFieldResponse {
  name: string
  type_hint: string
  description: string
}

export interface EntityRelationResponse {
  target: string
  relation: string
  description: string
}

export interface EntityResponse {
  name: string
  tier: 'core' | 'user'
  source: 'auto' | 'config' | 'api'
  definition: string
  fields: EntityFieldResponse[]
  constraints: string[]
  disambiguation: string
  relationships: EntityRelationResponse[]
  created_by: string
  created_at: string
  updated_at: string
}

export interface EntityVersionResponse {
  entity_id: string
  version: number
  content_hash: string
  snapshot: EntityResponse
  saved_by: string
  saved_at: string
}

export interface DriftAgentResponse {
  agent_id: string
  divergence_score: number
  details: string
}

export interface DriftReportResponse {
  entity_name: string
  divergence_score: number
  divergent_agents: DriftAgentResponse[]
  canonical_version: number
  recommendation: 'no_action' | 'notify' | 'retrain' | 'escalate'
}

export interface CreateEntityRequest {
  name: string
  definition?: string
  fields?: { name: string; type_hint: string; description?: string }[]
  constraints?: string[]
  disambiguation?: string
  relationships?: { target: string; relation: string; description?: string }[]
}

export interface UpdateEntityRequest {
  definition?: string
  fields?: { name: string; type_hint: string; description?: string }[]
  constraints?: string[]
  disambiguation?: string
  relationships?: { target: string; relation: string; description?: string }[]
}

// ── Endpoints ─────────────────────────────────────────────────

export async function listEntities(params?: {
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  cursor?: string | null
  limit?: number
  tier?: string
}): Promise<PaginatedResult<EntityResponse>> {
  const response = await apiClient.get<PaginatedResponse<EntityResponse>>('/ontology/entities', {
    params,
  })
  return unwrapPaginated<EntityResponse>(response)
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
  await apiClient.delete(`/ontology/entities/${encodeURIComponent(name)}`)
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

export async function triggerDriftCheck(): Promise<string> {
  const response = await apiClient.post<ApiResponse<string>>('/ontology/drift/check')
  return unwrap(response)
}
