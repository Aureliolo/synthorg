import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../../client'
import type { ApiResponse, PaginatedResponse } from '@/api/types/http'
import type { FailoverDeclaration, ProviderFailoverEvent } from '@/api/types/providers'

const BASE = '/providers/failover'

export async function getFailoverDeclaration(): Promise<FailoverDeclaration> {
  const response = await apiClient.get<ApiResponse<FailoverDeclaration>>(BASE)
  return unwrap(response)
}

export async function listFailoverEvents(
  options: {
    feature?: string
    declaredProvider?: string
    cursor?: string | null
    limit?: number
  } = {},
): Promise<PaginatedResult<ProviderFailoverEvent>> {
  const params: Record<string, string | number> = {}
  if (options.feature) params['feature'] = options.feature
  if (options.declaredProvider) params['declared_provider'] = options.declaredProvider
  if (options.cursor) params['cursor'] = options.cursor
  if (typeof options.limit === 'number') params['limit'] = options.limit
  const response = await apiClient.get<PaginatedResponse<ProviderFailoverEvent>>(
    `${BASE}-events`,
    { params },
  )
  return unwrapPaginated<ProviderFailoverEvent>(response)
}
