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

/**
 * Read-only contract: every domain that exposes version history
 * supports list / get / diff.  The type does not promise rollback;
 * use :class:`VersionHistoryClient` (the rollback-capable subtype)
 * for surfaces that wire a rollback action.
 */
export interface ReadOnlyVersionHistoryClient<T> {
  list: (
    options?: { cursor?: string | null; limit?: number },
  ) => Promise<PaginatedResult<VersionSnapshot<T>>>
  get: (version: number) => Promise<VersionSnapshot<T>>
  diff: (from: number, to: number) => Promise<VersionDiffResponse>
}

/**
 * Rollback-capable contract: extends the read-only surface with
 * ``rollback``.  Backed today only by the agent-identity domain;
 * other domains return a ``ReadOnlyVersionHistoryClient`` so the
 * type system surfaces the missing capability instead of letting
 * call sites hit a runtime 404 / 405.
 */
export interface VersionHistoryClient<T> extends ReadOnlyVersionHistoryClient<T> {
  rollback: (data: RollbackRequest) => Promise<VersionSnapshot<T>>
}

/**
 * Build a rollback-capable version-history client.  ``basePath`` is
 * the absolute controller path: ``"/agents/example-agent"`` for the
 * agent-identity domain.  Trailing slashes are not added.  Only
 * domains whose backend exposes a rollback endpoint should use this
 * factory; every other domain should use
 * :func:`createReadOnlyVersionHistoryClient`.
 */
export function createVersionHistoryClient<T>(
  basePath: string,
): VersionHistoryClient<T> {
  return {
    ...createReadOnlyVersionHistoryClient<T>(basePath),
    async rollback(data: RollbackRequest): Promise<VersionSnapshot<T>> {
      const response = await apiClient.post<ApiResponse<VersionSnapshot<T>>>(
        `${basePath}/versions/rollback`,
        data,
      )
      return unwrap(response)
    },
  }
}

/**
 * Build a read-only version-history client.  Use this for domains
 * whose backend exposes list / get / diff but no rollback (role
 * versions, budget-config versions, evaluation-config versions,
 * company versions today).
 */
export function createReadOnlyVersionHistoryClient<T>(
  basePath: string,
): ReadOnlyVersionHistoryClient<T> {
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
  }
}

// ── Domain-specific clients ───────────────────────────────────────────

/**
 * Build the role-versions client for ``roleName``.  Read-only on
 * the backend today.
 */
export function createRoleVersionsClient(roleName: string) {
  return createReadOnlyVersionHistoryClient<Record<string, unknown>>(
    `/roles/${encodeURIComponent(roleName)}`,
  )
}

/**
 * Singleton budget-config versions client.  Read-only.
 */
export const budgetConfigVersionsClient = createReadOnlyVersionHistoryClient<
  Record<string, unknown>
>('/budget/config')

/**
 * Singleton evaluation-config versions client.  Read-only.
 */
export const evaluationConfigVersionsClient = createReadOnlyVersionHistoryClient<
  Record<string, unknown>
>('/evaluation/config')

/**
 * Singleton company-structure versions client.  Read-only.
 */
export const companyVersionsClient = createReadOnlyVersionHistoryClient<
  Record<string, unknown>
>('/company')
