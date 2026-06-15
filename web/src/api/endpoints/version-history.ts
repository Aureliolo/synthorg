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
 *
 * Rollback is NOT uniform: the two rollback-capable domains diverge in
 * URL, request body, and return entity (agent identity posts
 * ``{target_version, reason?}`` to ``/<base>/versions/rollback`` and
 * returns ``AgentIdentity``; workflows post ``{target_version,
 * expected_revision}`` to ``/<base>/rollback`` and return
 * ``WorkflowDefinition``).  So the rollback action is supplied per
 * domain by the caller rather than synthesised from ``basePath``.
 *
 * This factory yields a typed client over the read paths.  The snapshot
 * shape is left generic so each domain can supply its own payload type.
 */
import {
  apiClient,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export interface VersionSnapshot<T> {
  /** Primary key of the versioned entity (stable across all its versions). */
  readonly entity_id: string
  /** Monotonic per-entity version counter (1-indexed); unique per entity. */
  readonly version: number
  readonly content_hash: string
  /** ISO-8601 timestamp the snapshot was saved. */
  readonly saved_at: string
  /** Actor that triggered the snapshot. */
  readonly saved_by: string
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

/**
 * Domain-agnostic rollback input collected by the shared
 * ``RollbackConfirmDialog``. Each domain's ``rollback`` function maps
 * this onto its own wire body (agent identity -> ``{target_version,
 * reason}``; workflow -> ``{target_version, expected_revision}``).
 */
export interface RollbackInput {
  /** Snapshot version number to restore the entity to. */
  readonly targetVersion: number
  /** Operator-supplied justification recorded in the audit trail. */
  readonly reason: string
}

/**
 * Per-domain rollback action. Resolves to the plain restored entity
 * (``AgentIdentity`` / ``WorkflowDefinition``); the shared dialog
 * discards the value, so it is typed ``unknown`` here. Callers that
 * need the entity should use the domain endpoint helper directly.
 */
export type RollbackFn = (input: RollbackInput) => Promise<unknown>

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
 * ``rollback``.  Backed by the agent-identity and workflow domains;
 * other domains return a ``ReadOnlyVersionHistoryClient`` so the
 * type system surfaces the missing capability instead of letting
 * call sites hit a runtime 404 / 405.
 */
export interface VersionHistoryClient<T> extends ReadOnlyVersionHistoryClient<T> {
  rollback: RollbackFn
}

/**
 * Build a rollback-capable version-history client.  ``basePath`` is the
 * absolute read path: ``"/agents/example-agent"`` for the agent-identity
 * domain, ``"/workflows/wf-1"`` for the workflow domain.  Trailing
 * slashes are not added.  ``rollback`` is the domain-specific rollback
 * action (it owns its own URL, request body, and return entity, which
 * the agent-identity and workflow backends define differently); supply
 * it via :func:`agents.rollbackAgentIdentity` /
 * :func:`workflows.rollbackWorkflow`.  Domains with no rollback endpoint
 * use :func:`createReadOnlyVersionHistoryClient`.
 */
export function createVersionHistoryClient<T>(
  basePath: string,
  rollback: RollbackFn,
): VersionHistoryClient<T> {
  return {
    ...createReadOnlyVersionHistoryClient<T>(basePath),
    rollback,
  }
}

/**
 * Build a read-only version-history client.  Use this for domains
 * whose backend exposes list / get / diff but no rollback (role
 * versions, budget-config versions, evaluation-config versions,
 * company versions today).
 */
function createReadOnlyVersionHistoryClient<T>(
  basePath: string,
): ReadOnlyVersionHistoryClient<T> {
  return {
    async list(
      options: { cursor?: string | null; limit?: number } = {},
    ): Promise<PaginatedResult<VersionSnapshot<T>>> {
      const params: Record<string, string | number> = {}
      if (options.cursor) params['cursor'] = options.cursor
      if (typeof options.limit === 'number') params['limit'] = options.limit
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

/**
 * Build the read-only role-versions client for ``roleName``.
 *
 * Per-role (like the per-workflow case) rather than a singleton, so it
 * is a factory. The role backend exposes list + get ONLY (no diff and
 * no rollback), so consumers MUST gate the diff affordance off via
 * ``VersionHistorySection``'s ``diffSupported={false}``.
 *
 * ``createReadOnlyVersionHistoryClient`` stays module-private: its only
 * callers are in this file, so exporting it would reintroduce the
 * dead-export Knip flagged. This factory is the public entry point.
 */
export function createRoleVersionsClient(
  roleName: string,
): ReadOnlyVersionHistoryClient<Record<string, unknown>> {
  return createReadOnlyVersionHistoryClient<Record<string, unknown>>(
    `/roles/${encodeURIComponent(roleName)}`,
  )
}
