import { http, HttpResponse } from 'msw'
import type {
  approveCharter,
  cancelCharter,
  editCharter,
  getCharter,
  listCharters,
  runInterviewTurn,
} from '@/api/endpoints/charter'
import type {
  CharterApprovalResult,
  InterviewTurnResult,
  ProjectCharter,
} from '@/api/types'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { paginatedFor, successFor } from './helpers'

export function buildCharter(
  overrides: Partial<ProjectCharter> = {},
): ProjectCharter {
  return {
    id: 'charter-default',
    conversation_id: 'conv-default',
    created_by: 'operator',
    version: 1,
    status: 'drafted',
    title: 'Better memory layer',
    brief: 'Build a self-hostable alternative to the incumbent memory tool.',
    goals: ['beat baseline recall'],
    constraints: ['self-hostable'],
    success_criteria: ['recall beats baseline by 10%'],
    scope: { in_scope: ['retrieval'], out_of_scope: ['billing'] },
    envelope: {
      amount: 5000,
      currency: DEFAULT_CURRENCY,
      deadline: null,
      time_horizon: '1 month',
    },
    project_id: null,
    proposed_project_name: 'memory-layer',
    proposed_project_description: 'A better memory layer.',
    created_at: '2026-05-22T00:00:00Z',
    updated_at: '2026-05-22T00:00:00Z',
    approved_at: null,
    approved_by: null,
    forecast_id: null,
    correlation_id: null,
    task_id: null,
    ...overrides,
  }
}

export const charterHandlers = [
  http.get('/api/v1/meta/charters', () => {
    const data = [buildCharter()]
    return HttpResponse.json(
      paginatedFor<typeof listCharters>({
        data,
        limit: 50,
        nextCursor: null,
        hasMore: false,
        pagination: { limit: 50, next_cursor: null, has_more: false },
      }),
    )
  }),
  http.get('/api/v1/meta/charters/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getCharter>(buildCharter({ id: String(params.id) })),
    ),
  ),
  http.post('/api/v1/meta/charters/interview', () => {
    const result: InterviewTurnResult = {
      conversation_id: 'conv-default',
      status: 'drafted',
      next_question: null,
      charter: buildCharter(),
      conversation_closed: false,
    }
    return HttpResponse.json(successFor<typeof runInterviewTurn>(result))
  }),
  http.patch('/api/v1/meta/charters/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof editCharter>(
        buildCharter({ id: String(params.id), version: 2 }),
      ),
    ),
  ),
  http.post('/api/v1/meta/charters/:id/approve', ({ params }) => {
    const charter = buildCharter({
      id: String(params.id),
      status: 'approved',
      approved_at: '2026-05-22T00:00:00Z',
      approved_by: 'operator',
      forecast_id: '6f1d4c2e-0000-4000-8000-000000000abc',
      correlation_id: 'conv-default',
      task_id: 'task-1',
    })
    const result: CharterApprovalResult = {
      charter,
      project_id: `charter-${String(params.id)}`,
      task_id: 'task-1',
      is_success: true,
    }
    return HttpResponse.json(successFor<typeof approveCharter>(result))
  }),
  http.post('/api/v1/meta/charters/:id/cancel', ({ params }) =>
    HttpResponse.json(
      successFor<typeof cancelCharter>(
        buildCharter({ id: String(params.id), status: 'cancelled' }),
      ),
    ),
  ),
]
