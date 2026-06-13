import { apiClient, unwrap, unwrapPaginated, withSignal } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

const REPORT_PERIOD_VALUES = ['daily', 'weekly', 'monthly'] as const satisfies readonly string[]
export type ReportPeriod = (typeof REPORT_PERIOD_VALUES)[number]

const REPORT_PERIOD_SET: ReadonlySet<string> = new Set(REPORT_PERIOD_VALUES)

function isReportPeriod(value: unknown): value is ReportPeriod {
  return typeof value === 'string' && REPORT_PERIOD_SET.has(value)
}

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
  // we discard the cursor metadata at the call site. The wire payload
  // is validated against ``REPORT_PERIOD_VALUES`` before narrowing so a
  // backend rolling out a new period cannot break exhaustive switches
  // downstream.
  const response = await apiClient.get<PaginatedResponse<string>>(
    '/reports/periods',
    withSignal(options.signal),
  )
  const periods = unwrapPaginated<string>(response).data
  if (!periods.every(isReportPeriod)) {
    throw new Error(
      `Unknown report period in /reports/periods response (allowed: ${REPORT_PERIOD_VALUES.join(', ')})`,
    )
  }
  return periods
}

export async function generateReport(
  period: ReportPeriod,
): Promise<ReportResponse> {
  // Same defensive narrowing as ``listReportPeriods``: the wire
  // payload's ``period`` is validated against ``REPORT_PERIOD_VALUES``
  // before the response is handed back as a typed ``ReportResponse``,
  // so a backend rolling out a new period cannot silently bypass
  // ``ReportPeriod`` and break exhaustive switches downstream.
  const response = await apiClient.post<ApiResponse<ReportResponse>>(
    '/reports/generate',
    { period } satisfies GenerateReportRequest,
  )
  const report = unwrap(response)
  if (!isReportPeriod(report.period)) {
    throw new Error(
      `Unknown report period in /reports/generate response (allowed: ${REPORT_PERIOD_VALUES.join(', ')})`,
    )
  }
  return report
}
