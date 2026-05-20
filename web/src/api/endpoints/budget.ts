import { apiClient, unwrap, type PaginatedResult, ApiRequestError } from '../client'
import type {
  AgentSpending,
  BudgetConfig,
  CostRecord,
  DailySummary,
  PeriodSummary,
} from '../types/budget'
import type { ParetoFrontier } from '../types'
import type { ErrorDetail } from '../types/errors'
import type { ApiResponse, PaginationParams } from '../types/http'

export interface CostRecordListResult extends PaginatedResult<CostRecord> {
  daily_summary: DailySummary[]
  period_summary: PeriodSummary
  currency: string
}

export async function getBudgetConfig(): Promise<BudgetConfig> {
  const response = await apiClient.get<ApiResponse<BudgetConfig>>('/budget/config')
  return unwrap(response)
}

export interface CostRecordListResponseBody {
  success: boolean
  data: CostRecord[]
  error?: string | null
  error_detail?: ErrorDetail | null
  pagination: {
    total: number | null
    offset: number
    limit: number
    next_cursor: string | null
    has_more: boolean
  }
  daily_summary: DailySummary[]
  period_summary: PeriodSummary
  currency: string
}

export async function listCostRecords(
  params?: PaginationParams & { agent_id?: string; task_id?: string },
): Promise<CostRecordListResult> {
  const response = await apiClient.get<CostRecordListResponseBody>('/budget/records', { params })
  const body = response.data
  if (!body?.success) {
    throw new ApiRequestError(body?.error ?? 'Unknown API error', body?.error_detail ?? null)
  }
  return {
    data: body.data,
    limit: body.pagination.limit,
    nextCursor: body.pagination.next_cursor,
    hasMore: body.pagination.has_more,
    pagination: {
      limit: body.pagination.limit,
      next_cursor: body.pagination.next_cursor,
      has_more: body.pagination.has_more,
    },
    daily_summary: body.daily_summary,
    period_summary: body.period_summary,
    currency: body.currency,
  }
}

export async function getAgentSpending(agentId: string): Promise<AgentSpending> {
  const response = await apiClient.get<ApiResponse<AgentSpending>>(`/budget/agents/${encodeURIComponent(agentId)}`)
  return unwrap(response)
}

export async function getParetoFrontier(): Promise<ParetoFrontier> {
  const response = await apiClient.get<ParetoFrontier>('/budget/pareto')
  return response.data
}
