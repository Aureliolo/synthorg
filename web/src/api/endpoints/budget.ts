import { apiClient, unwrap, withSignal, type PaginatedResult, ApiRequestError } from '../client'
import type {
  AgentSpending,
  BudgetConfig,
  CostRecord,
  DailySummary,
  PeriodSummary,
} from '../types/budget'
import type {
  AnalyticsAggregation,
  Forecast,
  ForecastRequest,
  ForecastApproveRequest,
  ForecastRejectRequest,
  ParetoFrontier,
  RaiseCeilingRequest,
} from '../types'
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
    limit: number
    next_cursor: string | null
    has_more: boolean
  }
  daily_summary: DailySummary[]
  period_summary: PeriodSummary
  currency: string
}

/**
 * Validate the nested shape of a ``success: true`` cost-record envelope. The
 * server can return a malformed body at runtime, so the declared field types
 * are a boundary lie; guard the fields ``listCostRecords`` dereferences below.
 */
function isCostRecordEnvelopeShaped(body: CostRecordListResponseBody): boolean {
  const envelope = body as {
    data?: unknown
    pagination?: { limit?: unknown }
    daily_summary?: unknown
    period_summary?: unknown
    currency?: unknown
  }
  return (
    Array.isArray(envelope.data) &&
    typeof envelope.pagination?.limit === 'number' &&
    Array.isArray(envelope.daily_summary) &&
    envelope.period_summary != null &&
    typeof envelope.currency === 'string'
  )
}

export async function listCostRecords(
  params?: PaginationParams & { agent_id?: string; task_id?: string },
): Promise<CostRecordListResult> {
  const response = await apiClient.get<CostRecordListResponseBody>('/budget/records', { params })
  // Axios types ``response.data`` as the declared envelope, but the server
  // can return a malformed / empty body at runtime; widen the boundary so the
  // optional-chain guards below are real, not dead.
  const body = response.data as CostRecordListResponseBody | null | undefined
  if (!body?.success) {
    throw new ApiRequestError(body?.error ?? 'Unknown API error', body?.error_detail ?? null)
  }
  // A ``success: true`` envelope can still arrive with missing / malformed
  // nested fields; validate the shape before dereferencing so a bad payload
  // surfaces as ``ApiRequestError`` rather than a raw ``TypeError``.
  if (!isCostRecordEnvelopeShaped(body)) {
    throw new ApiRequestError('Unexpected API response format')
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

export async function getParetoFrontier(signal?: AbortSignal): Promise<ParetoFrontier> {
  const response = await apiClient.get<ApiResponse<ParetoFrontier>>('/budget/pareto', withSignal(signal))
  return unwrap(response)
}

export async function getCallAnalytics(
  filters?: { agent_id?: string; task_id?: string; provider?: string },
  signal?: AbortSignal,
): Promise<AnalyticsAggregation> {
  const response = await apiClient.get<ApiResponse<AnalyticsAggregation>>(
    '/budget/call-analytics',
    withSignal(signal, { params: filters }),
  )
  return unwrap(response)
}

export async function createForecast(data: ForecastRequest): Promise<Forecast> {
  const response = await apiClient.post<ApiResponse<Forecast>>('/budget/forecast', data)
  return unwrap(response)
}

export async function getForecast(forecastId: string): Promise<Forecast> {
  const response = await apiClient.get<ApiResponse<Forecast>>(
    `/budget/forecasts/${encodeURIComponent(forecastId)}`,
  )
  return unwrap(response)
}

export async function approveForecast(
  forecastId: string,
  data: ForecastApproveRequest,
): Promise<Forecast> {
  const response = await apiClient.post<ApiResponse<Forecast>>(
    `/budget/forecasts/${encodeURIComponent(forecastId)}/approve`,
    data,
  )
  return unwrap(response)
}

export async function rejectForecast(
  forecastId: string,
  data: ForecastRejectRequest,
): Promise<Forecast> {
  const response = await apiClient.post<ApiResponse<Forecast>>(
    `/budget/forecasts/${encodeURIComponent(forecastId)}/reject`,
    data,
  )
  return unwrap(response)
}

export async function raiseCeiling(
  forecastId: string,
  data: RaiseCeilingRequest,
): Promise<Forecast> {
  const response = await apiClient.post<ApiResponse<Forecast>>(
    `/budget/forecasts/${encodeURIComponent(forecastId)}/raise_ceiling`,
    data,
  )
  return unwrap(response)
}
