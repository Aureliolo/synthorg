import { http, HttpResponse } from 'msw'
import type {
  cancelEscalation,
  getEscalation,
  listEscalations,
  submitEscalationDecision,
} from '@/api/endpoints/escalations'
import type { Escalation, EscalationResponse } from '@/api/types/escalations'
import { emptyPage, paginatedFor, successFor } from './helpers'

function buildEscalation(
  overrides: Partial<Escalation> = {},
): Escalation {
  return {
    id: 'esc-default',
    conflict: {
      id: 'conflict-default',
      type: 'resource',
      task_id: null,
      subject: 'Default conflict',
      positions: [],
      detected_at: '2026-04-19T00:00:00Z',
      is_cross_department: false,
    },
    status: 'pending',
    created_at: '2026-04-19T00:00:00Z',
    expires_at: null,
    decided_at: null,
    decided_by: null,
    decision: null,
    ...overrides,
  }
}

function buildResponse(
  overrides: Partial<EscalationResponse> = {},
): EscalationResponse {
  const esc = overrides.escalation ?? buildEscalation()
  return {
    ...overrides,
    escalation: esc,
    conflict_id: overrides.conflict_id ?? esc.conflict.id,
    status: overrides.status ?? esc.status,
  }
}

export const escalationsHandlers = [
  http.get('/api/v1/conflicts/escalations', () =>
    HttpResponse.json(paginatedFor<typeof listEscalations>(emptyPage())),
  ),
  http.get('/api/v1/conflicts/escalations/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getEscalation>(
        buildResponse({
          escalation: buildEscalation({ id: String(params['id']) }),
        }),
      ),
    ),
  ),
  http.post('/api/v1/conflicts/escalations/:id/decision', ({ params }) =>
    HttpResponse.json(
      successFor<typeof submitEscalationDecision>(
        buildResponse({
          escalation: buildEscalation({
            id: String(params['id']),
            status: 'decided',
            decided_at: '2026-04-19T00:00:00Z',
            decided_by: 'user-1',
          }),
          status: 'decided',
        }),
      ),
    ),
  ),
  http.post('/api/v1/conflicts/escalations/:id/cancel', ({ params }) =>
    HttpResponse.json(
      successFor<typeof cancelEscalation>(
        buildResponse({
          escalation: buildEscalation({
            id: String(params['id']),
            status: 'cancelled',
          }),
          status: 'cancelled',
        }),
      ),
    ),
  ),
]
