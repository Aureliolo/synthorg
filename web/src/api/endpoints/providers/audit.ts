import {
  apiClient,
  unwrapPaginated,
  type PaginatedResult,
} from '../../client'
import type { PaginatedResponse, ProviderAuditEvent } from '@/api/types'

export async function listProviderAudit(
  name: string,
  options: { cursor?: string | null; limit?: number } = {},
): Promise<PaginatedResult<ProviderAuditEvent>> {
  const params: Record<string, string | number> = {}
  if (options.cursor) params.cursor = options.cursor
  if (typeof options.limit === 'number') params.limit = options.limit
  const response = await apiClient.get<PaginatedResponse<ProviderAuditEvent>>(
    `/providers/${encodeURIComponent(name)}/audit`,
    { params },
  )
  return unwrapPaginated<ProviderAuditEvent>(response)
}
