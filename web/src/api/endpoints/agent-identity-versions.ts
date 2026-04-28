/**
 * Agent identity version-history client.
 *
 * Reads list / get / diff and submits rollback against the
 * ``/agents/{id}/versions`` REST surface.  Mirrors the workflow-editor
 * versioning pattern: cursor-paginated list, single-version fetch,
 * (from, to) diff, one-click rollback.
 */
import {
  apiClient,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export interface AgentIdentitySnapshot {
  readonly id: string
  readonly version: number
  readonly created_at: string
  readonly content_hash: string
  readonly snapshot: Record<string, unknown>
}

export interface VersionDiffEntry {
  readonly path: string
  readonly before: unknown
  readonly after: unknown
}

export interface VersionDiffResponse {
  readonly from_version: number
  readonly to_version: number
  readonly entries: readonly VersionDiffEntry[]
}

export interface RollbackRequest {
  readonly to_version: number
  readonly reason: string
}

export async function listAgentIdentityVersions(
  agentId: string,
  options: { cursor?: string | null; limit?: number } = {},
): Promise<PaginatedResult<AgentIdentitySnapshot>> {
  const params: Record<string, string | number> = {}
  if (options.cursor) params.cursor = options.cursor
  if (typeof options.limit === 'number') params.limit = options.limit
  const response = await apiClient.get<PaginatedResponse<AgentIdentitySnapshot>>(
    `/agents/${encodeURIComponent(agentId)}/versions`,
    { params },
  )
  return unwrapPaginated<AgentIdentitySnapshot>(response)
}

export async function getAgentIdentityVersion(
  agentId: string,
  version: number,
): Promise<AgentIdentitySnapshot> {
  const response = await apiClient.get<ApiResponse<AgentIdentitySnapshot>>(
    `/agents/${encodeURIComponent(agentId)}/versions/${version}`,
  )
  return unwrap(response)
}

export async function getAgentIdentityDiff(
  agentId: string,
  fromVersion: number,
  toVersion: number,
): Promise<VersionDiffResponse> {
  const response = await apiClient.get<ApiResponse<VersionDiffResponse>>(
    `/agents/${encodeURIComponent(agentId)}/versions/diff`,
    { params: { from_version: fromVersion, to_version: toVersion } },
  )
  return unwrap(response)
}

export async function rollbackAgentIdentity(
  agentId: string,
  data: RollbackRequest,
): Promise<AgentIdentitySnapshot> {
  const response = await apiClient.post<ApiResponse<AgentIdentitySnapshot>>(
    `/agents/${encodeURIComponent(agentId)}/versions/rollback`,
    data,
  )
  return unwrap(response)
}
