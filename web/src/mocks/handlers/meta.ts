import { http, HttpResponse } from 'msw'
import type {
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  postChat,
  postChatPropose,
} from '@/api/endpoints/meta'
import { apiError, successFor } from './helpers'

export const metaHandlers = [
  http.get('/api/v1/meta/config', () =>
    HttpResponse.json(
      successFor<typeof getMetaConfig>({
        enabled: false,
        chief_of_staff_enabled: false,
        config_tuning_enabled: false,
        architecture_proposals_enabled: false,
        prompt_tuning_enabled: false,
        code_modification_enabled: false,
      }),
    ),
  ),
  http.get('/api/v1/meta/proposals', () =>
    HttpResponse.json(successFor<typeof listProposals>([])),
  ),
  http.get('/api/v1/meta/signals', () =>
    HttpResponse.json(
      successFor<typeof getSignals>({ enabled: false, domains: [] }),
    ),
  ),
  http.get('/api/v1/meta/ab-tests', () =>
    HttpResponse.json(successFor<typeof listABTests>([])),
  ),
  http.post('/api/v1/meta/chat/propose', async ({ request }) => {
    let body: unknown
    try {
      body = await request.json()
    } catch {
      return HttpResponse.json(apiError('Message must not be blank'), {
        status: 400,
      })
    }
    if (
      !body ||
      typeof body !== 'object' ||
      typeof (body as { message?: unknown }).message !== 'string' ||
      !(body as { message: string }).message.trim()
    ) {
      return HttpResponse.json(apiError('Message must not be blank'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof postChatPropose>({
        conversation_id: 'conv-mock-001',
        status: 'proposed',
        clarifying_question: null,
        conversation_closed: false,
        proposals: [
          {
            approval_id: 'appr-mock-001',
            proposal_id: 'prop-mock-001',
            title: 'Mock proposed work',
            task_type: 'development',
            priority: 'medium',
          },
        ],
      }),
    )
  }),
  http.post('/api/v1/meta/chat', async ({ request }) => {
    let body: unknown
    try {
      body = await request.json()
    } catch {
      return HttpResponse.json(apiError('Question must not be blank'), {
        status: 400,
      })
    }
    if (
      !body ||
      typeof body !== 'object' ||
      typeof (body as { question?: unknown }).question !== 'string' ||
      !(body as { question: string }).question.trim()
    ) {
      return HttpResponse.json(apiError('Question must not be blank'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof postChat>({
        answer: 'default response',
        sources: [],
        confidence: 0,
      }),
    )
  }),
]
