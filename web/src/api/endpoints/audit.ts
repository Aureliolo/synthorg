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

export async function listAuditEntries(
  params: ListAuditEntriesParams = {},
): Promise<PaginatedResult<AuditEntry>> {
  const query = new URLSearchParams()
  if (params.agentId) query.set('agent_id', params.agentId)
  if (params.toolName) query.set('tool_name', params.toolName)
  if (params.actionType) query.set('action_type', params.actionType)
  if (params.verdict) query.set('verdict', params.verdict)
  if (params.since) query.set('since', params.since)
  if (params.until) query.set('until', params.until)
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.limit != null) query.set('limit', String(params.limit))
  const qs = query.toString()
  const url = qs ? `/security/audit?${qs}` : '/security/audit'
  const response = await apiClient.get<PaginatedResponse<AuditEntry>>(url)
  return unwrapPaginated(response)
}
