import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { components } from '../types/generated'
import type { ApiResponse, PaginatedResponse } from '../types/http'

type Schemas = components['schemas']

export type ReportPeriod = Schemas['ReportPeriod']
export type ReportResponse = Schemas['ReportResponse']
export type GenerateReportRequest = Schemas['GenerateReportRequest']

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
