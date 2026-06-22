import { apiClient, paginateAll, unwrap, unwrapPaginated, unwrapVoid } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

// -- Types -------------------------------------------------------------------

export type Comparator = 'lt' | 'le' | 'gt' | 'ge' | 'eq' | 'ne'
export type RuleSeverity = 'info' | 'warning' | 'critical'
export type ProposalAltitude = 'config_tuning' | 'architecture' | 'prompt_tuning'

export interface MetricDescriptor {
  path: string
  label: string
  domain: string
  value_type: 'float' | 'int'
  min_value: number | null
  max_value: number | null
  unit: string | null
  nullable: boolean
}

export interface CustomRule {
  id: string
  name: string
  description: string
  metric_path: string
  comparator: Comparator
  threshold: number
  severity: RuleSeverity
  target_altitudes: readonly ProposalAltitude[]
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface RuleListItem {
  name: string
  enabled: boolean
  target_altitudes: readonly string[]
  type: 'builtin' | 'custom'
  id?: string
  description?: string
  metric_path?: string
  comparator?: string
  threshold?: number
  severity?: string
}

export interface CreateCustomRuleRequest {
  name: string
  description: string
  metric_path: string
  comparator: Comparator
  threshold: number
  severity: RuleSeverity
  target_altitudes: ProposalAltitude[]
}

export interface PreviewRequest {
  metric_path: string
  comparator: Comparator
  threshold: number
  sample_value: number
}

export interface PreviewMatch {
  rule_name: string
  severity: string
  description: string
  signal_context: Record<string, unknown>
}

export interface PreviewResult {
  would_fire: boolean
  match: PreviewMatch | null
}

// -- API functions -----------------------------------------------------------

const BASE = '/meta/custom-rules'

export async function listCustomRules(): Promise<readonly CustomRule[]> {
  // Backend returns ``PaginatedResponse[CustomRule]`` (it collects all rules
  // server-side before paginating). Walk every cursor page rather than
  // unwrapping the first page only, so large installations are not truncated.
  return paginateAll<CustomRule>((cursor) =>
    apiClient
      .get<PaginatedResponse<CustomRule>>(BASE, {
        params: cursor !== null ? { cursor } : undefined,
      })
      .then(unwrapPaginated<CustomRule>),
  )
}

export async function getCustomRule(id: string): Promise<CustomRule> {
  const response = await apiClient.get<ApiResponse<CustomRule>>(
    `${BASE}/${encodeURIComponent(id)}`,
  )
  return unwrap(response)
}

export async function createCustomRule(
  data: CreateCustomRuleRequest,
): Promise<CustomRule> {
  const response = await apiClient.post<ApiResponse<CustomRule>>(BASE, data)
  return unwrap(response)
}

export async function updateCustomRule(
  id: string,
  data: Partial<CreateCustomRuleRequest>,
): Promise<CustomRule> {
  const response = await apiClient.patch<ApiResponse<CustomRule>>(
    `${BASE}/${encodeURIComponent(id)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteCustomRule(id: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `${BASE}/${encodeURIComponent(id)}`,
  )
  unwrapVoid(response)
}

export async function toggleCustomRule(id: string): Promise<CustomRule> {
  const response = await apiClient.post<ApiResponse<CustomRule>>(
    `${BASE}/${encodeURIComponent(id)}/toggle`,
  )
  return unwrap(response)
}

export async function listMetrics(): Promise<MetricDescriptor[]> {
  // Backend returns ``PaginatedResponse[MetricDescriptor]``; the
  // metric registry is bounded but paginated for shape consistency
  // with the rest of the list surface. Default page size covers the
  // full registry, so the caller receives a flat array.
  const response = await apiClient.get<PaginatedResponse<MetricDescriptor>>(
    `${BASE}/metrics`,
  )
  return unwrapPaginated<MetricDescriptor>(response).data
}

export async function previewRule(
  data: PreviewRequest,
): Promise<PreviewResult> {
  const response = await apiClient.post<ApiResponse<PreviewResult>>(
    `${BASE}/preview`,
    data,
  )
  return unwrap(response)
}

export async function listAllRules(): Promise<RuleListItem[]> {
  const response = await apiClient.get<ApiResponse<RuleListItem[]>>(
    '/meta/rules',
  )
  return unwrap(response)
}
