import { http, HttpResponse } from 'msw'
import type {
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  postChat,
  postChatGroup,
  postChatPropose,
} from '@/api/endpoints/meta'
import { apiError, successFor } from './helpers'

function _hasBlankMessage(body: unknown): boolean {
  return (
    !body ||
    typeof body !== 'object' ||
    typeof (body as { message?: unknown }).message !== 'string' ||
    !(body as { message: string }).message.trim()
  )
}

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
    if (_hasBlankMessage(body)) {
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
        // Concern routing (#1969) is off by default: the generic Chief
        // of Staff answers, so no role attribution is carried.
        responder_role: null,
        responder_name: null,
        routed_topic: null,
        routing_confidence: null,
      }),
    )
  }),
  http.post('/api/v1/meta/chat/group', async ({ request }) => {
    let body: unknown
    try {
      body = await request.json()
    } catch {
      return HttpResponse.json(apiError('Message must not be blank'), {
        status: 400,
      })
    }
    if (_hasBlankMessage(body)) {
      return HttpResponse.json(apiError('Message must not be blank'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof postChatGroup>({
        conversation_id: 'conv-grp-mock-001',
        contributions: [
          {
            agent_id: 'agent-ceo-mock',
            agent_name: 'Dana',
            participant_role: 'CEO',
            content: 'We should prioritise the enterprise segment.',
            sequence: 1,
            input_tokens: 80,
            output_tokens: 30,
          },
          {
            agent_id: 'agent-cfo-mock',
            agent_name: 'Casey',
            participant_role: 'CFO',
            content: 'That needs a larger sales budget; I can model it.',
            sequence: 2,
            input_tokens: 90,
            output_tokens: 28,
          },
        ],
        participants: [
          {
            id: 'part-ceo-mock',
            conversation_id: 'conv-grp-mock-001',
            agent_id: 'agent-ceo-mock',
            agent_name: 'Dana',
            participant_role: 'CEO',
            status: 'active',
            added_by: 'user-mock',
            added_at: '2026-05-19T09:00:00Z',
          },
          {
            id: 'part-cfo-mock',
            conversation_id: 'conv-grp-mock-001',
            agent_id: 'agent-cfo-mock',
            agent_name: 'Casey',
            participant_role: 'CFO',
            status: 'active',
            added_by: 'user-mock',
            added_at: '2026-05-19T09:00:00.000001Z',
          },
        ],
        participants_skipped: [],
        truncated_reason: null,
        // Agent-initiated invites (#1971) are off by default; the happy
        // path parks none, so the consent surface stays empty.
        pending_invites: [],
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
