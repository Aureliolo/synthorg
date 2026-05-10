import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export const REPORT_PERIOD_VALUES = ['daily', 'weekly', 'monthly'] as const
export type ReportPeriod = (typeof REPORT_PERIOD_VALUES)[number]

export interface ReportResponse {
  period: ReportPeriod
  start: string
  end: string
  has_spending: boolean
  has_performance: boolean
  has_task_completion: boolean
  has_risk_trends: boolean
  generated_at: string
}

export interface GenerateReportRequest {
  period: ReportPeriod
}

export interface ListReportPeriodsOptions {
  signal?: AbortSignal
}

export async function listReportPeriods(
  options: ListReportPeriodsOptions = {},
): Promise<ReportPeriod[]> {
  // Backend returns ``PaginatedResponse[ReportPeriod]`` (the period set
  // is bounded but paginated for shape consistency with the rest of
  // the list surface). Default page size covers all known periods, so
  // we discard the cursor metadata at the call site.
  const response = await apiClient.get<PaginatedResponse<ReportPeriod>>(
    '/reports/periods',
    { signal: options.signal },
  )
  return unwrapPaginated<ReportPeriod>(response).data
}

export async function generateReport(
  period: ReportPeriod,
): Promise<ReportResponse> {
  const response = await apiClient.post<ApiResponse<ReportResponse>>(
    '/reports/generate',
    { period } satisfies GenerateReportRequest,
  )
  return unwrap(response)
}
