/**
 * Admin audit-log endpoints.
 *
 * Wraps GET /security/audit. The backend exposes rich filters
 * (agent_id, tool_name, action_type, verdict, since, until, JSONB
 * containment); the admin viewer surfaces the common subset.
 */
import { apiClient, unwrapPaginated } from '../client'
import type { PaginatedResponse } from '../types/http'
import type { AuditEntry } from '@/api/types'
import type { PaginatedResult } from '../client'

export interface ListAuditEntriesParams {
  agentId?: string | null
  toolName?: string | null
  actionType?: string | null
  verdict?: string | null
  since?: string | null
  until?: string | null
  cursor?: string | null
  limit?: number | null
}

const AUDIT_FILTER_KEYS = [
  ['agentId', 'agent_id'],
  ['toolName', 'tool_name'],
  ['actionType', 'action_type'],
  ['verdict', 'verdict'],
  ['since', 'since'],
  ['until', 'until'],
  ['cursor', 'cursor'],
] as const satisfies ReadonlyArray<readonly [keyof ListAuditEntriesParams, string]>

function _buildAuditQuery(params: ListAuditEntriesParams): URLSearchParams {
  const query = new URLSearchParams()
  for (const [optionKey, wireKey] of AUDIT_FILTER_KEYS) {
    const value = params[optionKey]
    if (typeof value === 'string' && value) query.set(wireKey, value)
  }
  if (params.limit != null) query.set('limit', String(params.limit))
  return query
}

export async function listAuditEntries(
  params: ListAuditEntriesParams = {},
): Promise<PaginatedResult<AuditEntry>> {
  const qs = _buildAuditQuery(params).toString()
  const url = qs ? `/security/audit?${qs}` : '/security/audit'
  const response = await apiClient.get<PaginatedResponse<AuditEntry>>(url)
  return unwrapPaginated(response)
}
