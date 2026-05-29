import { http, HttpResponse } from 'msw'
import type {
  generateReport,
  listReportPeriods,
  ReportResponse,
} from '@/api/endpoints/reports'
import { paginatedEnvelopeFor, successFor } from './helpers'

/**
 * Build a happy-path ``ReportResponse`` for stories and tests.
 * Mirrors the backend Pydantic model field-for-field.
 */
function buildReportResponse(
  overrides: Partial<ReportResponse> = {},
): ReportResponse {
  return {
    period: 'monthly',
    start: '2026-04-01T00:00:00Z',
    end: '2026-04-30T23:59:59Z',
    has_spending: true,
    has_performance: true,
    has_task_completion: true,
    has_risk_trends: true,
    generated_at: '2026-05-01T00:00:00Z',
    ...overrides,
  }
}

export const reportsHandlers = [
  http.get('/api/v1/reports/periods', () =>
    HttpResponse.json(
      paginatedEnvelopeFor<typeof listReportPeriods>(['daily', 'weekly', 'monthly']),
    ),
  ),
  http.post('/api/v1/reports/generate', () =>
    HttpResponse.json(successFor<typeof generateReport>(buildReportResponse())),
  ),
]
