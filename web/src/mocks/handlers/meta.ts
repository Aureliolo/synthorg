import { http, HttpResponse } from 'msw'
import type {
  EvolutionAxisStat,
  getEvolutionSummary,
  getMetaConfig,
  getSignals,
  listABTests,
  listAlerts,
  listEvolutionOutcomes,
  listProposals,
  getConversationTurns,
  listConversations,
  postTurn,
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
        chief_of_staff: {
          chat_enabled: true,
          propose_enabled: true,
          group_chat_enabled: true,
          direct_mcp_enabled: false,
          chat_model: 'test-provider/example-medium-001',
          propose_model: 'test-provider/example-large-001',
          routing_model: 'test-provider/example-small-001',
          narrative_model: 'test-provider/example-medium-001',
          direct_mcp_ready: false,
        },
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
  http.get('/api/v1/meta/alerts', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listAlerts>([])),
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
  http.post('/api/v1/meta/chat/turn', async ({ request }) => {
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
    // Default happy path: the org classifies an ambiguous message as a
    // read-only question and answers it. Tests that exercise a specific
    // capability override this with their own ``server.use(...)``.
    return HttpResponse.json(
      successFor<typeof postTurn>({
        intent: 'explain',
        intent_reason: 'no_intent_classifier',
        intent_confidence: null,
        conversation_id: null,
        answer: {
          answer: 'The organisation is healthy.',
          sources: [],
          cited_records: [],
          confidence: 0.8,
        },
        propose: null,
        group: null,
        act: null,
        charter: null,
        chime_ins: [],
      }),
    )
  }),
  http.get('/api/v1/meta/chat/conversations', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listConversations>([])),
  ),
  http.get('/api/v1/meta/chat/conversations/:id', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof getConversationTurns>([])),
  ),
]
