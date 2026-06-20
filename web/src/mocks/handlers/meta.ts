import { http, HttpResponse } from 'msw'
import type {
  EvolutionAxisStat,
  getEvolutionSummary,
  getMetaConfig,
  getSignals,
  listABTests,
  listEvolutionOutcomes,
  listProposals,
  postChat,
  postChatAct,
  postChatGroup,
  postChatPropose,
} from '@/api/endpoints/meta'
import { apiSuccess, apiError, paginatedEnvelopeFor, successFor } from './helpers'

function _hasBlankField(body: unknown, field: string): boolean {
  if (!body || typeof body !== 'object') return true
  const value = (body as Record<string, unknown>)[field]
  return typeof value !== 'string' || !value.trim()
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
    HttpResponse.json(paginatedEnvelopeFor<typeof listProposals>([])),
  ),
  http.get('/api/v1/meta/signals', () =>
    HttpResponse.json(
      successFor<typeof getSignals>({ enabled: false, domains: [] }),
    ),
  ),
  http.get('/api/v1/meta/ab-tests', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listABTests>([])),
  ),
  http.get('/api/v1/meta/evolution/summary', () =>
    HttpResponse.json(
      successFor<typeof getEvolutionSummary>({
        total_proposals: 0,
        approval_rate: 0,
        most_adapted_axis: null,
        recent_outcomes: [],
      }),
    ),
  ),
  http.get('/api/v1/meta/evolution/outcomes', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listEvolutionOutcomes>([])),
  ),
  http.get('/api/v1/meta/evolution/axes/stats', () =>
    // ``getEvolutionAxisStats`` flattens ``.axes`` from the envelope, so its
    // return type is ``EvolutionAxisStat[]`` and ``successFor<typeof fn>``
    // cannot express the nested wire payload. Pin the envelope's data type
    // explicitly so the mock still turns red if ``EvolutionAxisStat`` drifts.
    HttpResponse.json(apiSuccess<{ axes: EvolutionAxisStat[] }>({ axes: [] })),
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
    if (_hasBlankField(body, 'message')) {
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
        // Concern routing is off by default: the generic Chief
        // of Staff answers, so no role attribution is carried.
        responder_role: null,
        responder_name: null,
        routed_topic: null,
        routing_confidence: null,
        steering: [],
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
    if (_hasBlankField(body, 'message')) {
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
            id: '11111111-1111-4111-8111-111111111111',
            conversation_id: 'conv-grp-mock-001',
            agent_id: 'agent-ceo-mock',
            agent_name: 'Dana',
            participant_role: 'CEO',
            status: 'active',
            added_by: 'user-mock',
            added_at: '2026-05-19T09:00:00Z',
          },
          {
            id: '22222222-2222-4222-8222-222222222222',
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
        // Agent-initiated invites are off by default; the happy
        // path parks none, so the consent surface stays empty.
        pending_invites: [],
      }),
    )
  }),
  http.post('/api/v1/meta/chat/act', async ({ request }) => {
    let body: unknown
    try {
      body = await request.json()
    } catch {
      return HttpResponse.json(apiError('Instruction must not be blank'), {
        status: 400,
      })
    }
    if (_hasBlankField(body, 'instruction')) {
      return HttpResponse.json(apiError('Instruction must not be blank'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof postChatAct>({
        agent_id: 'agent-cfo-mock',
        agent_name: 'Casey',
        conversation_id: 'conv-act-mock-001',
        // Direct MCP acting is off by default; the happy path
        // performs a permitted action under trust and completes.
        action: {
          termination_reason: 'completed',
          final_message: 'Done -- revenue is up 4% this week.',
          tool_calls: [
            { tool_name: 'query_metrics', is_error: false, result: 'revenue +4%' },
          ],
          approval_id: null,
          parked: false,
        },
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
    if (_hasBlankField(body, 'question')) {
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
