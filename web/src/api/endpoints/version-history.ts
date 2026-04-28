/**
 * Generic version-history client.
 *
 * The five domains that expose version snapshots (agent identity,
 * role, budget config, evaluation config, company) all follow the
 * same wire shape:
 *
 * * ``GET /<base>/versions`` -- cursor-paginated list
 * * ``GET /<base>/versions/{n}`` -- single version
 * * ``GET /<base>/versions/diff?from_version=&to_version=`` -- diff
 * * ``POST /<base>/versions/rollback`` -- (only on domains that
 *   support it; agent identity is the only one today).
 *
 * This factory yields a typed client over any of those paths.  The
 * snapshot shape is left generic so each domain can supply its own
 * payload type.
 */
import {
  apiClient,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export interface VersionSnapshot<T> {
  readonly id: string
  readonly version: number
  readonly created_at: string
  readonly content_hash: string
  readonly snapshot: T
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

export interface VersionHistoryClient<T> {
  list: (
    options?: { cursor?: string | null; limit?: number },
  ) => Promise<PaginatedResult<VersionSnapshot<T>>>
  get: (version: number) => Promise<VersionSnapshot<T>>
  diff: (from: number, to: number) => Promise<VersionDiffResponse>
  rollback: (data: RollbackRequest) => Promise<VersionSnapshot<T>>
}

/**
 * Build a version-history client.  ``basePath`` is the absolute
 * controller path: ``"/roles/research"`` for a role-scoped client,
 * ``"/budget/config"`` for a singleton-scoped one.  Trailing slashes
 * are not added.
 */
export function createVersionHistoryClient<T>(
  basePath: string,
): VersionHistoryClient<T> {
  return {
    async list(
      options: { cursor?: string | null; limit?: number } = {},
    ): Promise<PaginatedResult<VersionSnapshot<T>>> {
      const params: Record<string, string | number> = {}
      if (options.cursor) params.cursor = options.cursor
      if (typeof options.limit === 'number') params.limit = options.limit
      const response = await apiClient.get<
        PaginatedResponse<VersionSnapshot<T>>
      >(`${basePath}/versions`, { params })
      return unwrapPaginated<VersionSnapshot<T>>(response)
    },

    async get(version: number): Promise<VersionSnapshot<T>> {
      const response = await apiClient.get<ApiResponse<VersionSnapshot<T>>>(
        `${basePath}/versions/${version}`,
      )
      return unwrap(response)
    },

    async diff(from: number, to: number): Promise<VersionDiffResponse> {
      const response = await apiClient.get<ApiResponse<VersionDiffResponse>>(
        `${basePath}/versions/diff`,
        { params: { from_version: from, to_version: to } },
      )
      return unwrap(response)
    },

    async rollback(data: RollbackRequest): Promise<VersionSnapshot<T>> {
      const response = await apiClient.post<ApiResponse<VersionSnapshot<T>>>(
        `${basePath}/versions/rollback`,
        data,
      )
      return unwrap(response)
    },
  }
}

// ── Domain-specific clients ───────────────────────────────────────────

/**
 * Build the role-versions client for ``roleName``.
 *
 * Read-only on the backend today (no rollback endpoint); the
 * ``rollback`` action will surface a 404 / 405 if invoked.
 */
export function createRoleVersionsClient(roleName: string) {
  return createVersionHistoryClient<Record<string, unknown>>(
    `/roles/${encodeURIComponent(roleName)}`,
  )
}

/**
 * Singleton budget-config versions client.
 *
 * Read-only on the backend today.
 */
export const budgetConfigVersionsClient = createVersionHistoryClient<
  Record<string, unknown>
>('/budget/config')

/**
 * Singleton evaluation-config versions client.  Read-only.
 */
export const evaluationConfigVersionsClient = createVersionHistoryClient<
  Record<string, unknown>
>('/evaluation/config')

/**
 * Singleton company-structure versions client.  Read-only.
 */
export const companyVersionsClient = createVersionHistoryClient<
  Record<string, unknown>
>('/company')
