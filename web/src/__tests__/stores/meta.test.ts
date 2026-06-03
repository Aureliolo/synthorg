import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ErrorCategory, ErrorCode } from '@/api/types/errors'
import { useMetaStore } from '@/stores/meta'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'

/** A SERVICE_UNAVAILABLE (503) body with a curated operator-facing detail. */
function serviceUnavailable(detail: string) {
  return apiError(detail, {
    error_code: ErrorCode.SERVICE_UNAVAILABLE,
    error_category: ErrorCategory.INTERNAL,
    detail,
  })
}

function resetStore() {
  useMetaStore.setState({
    config: null,
    proposals: [],
    abTests: [],
    signals: null,
    activeAgents: [],
    loading: false,
    error: null,
    chatLoading: false,
    proposeLoading: false,
    groupChatLoading: false,
    actionLoading: false,
  })
  useToastStore.setState({ toasts: [] })
}

beforeEach(() => {
  resetStore()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('fetchProposals', () => {
  it('stores proposals and clears error on success', async () => {
    server.use(
      http.get('/api/v1/meta/proposals', () =>
        HttpResponse.json(apiSuccess([])),
      ),
    )
    useMetaStore.setState({ error: 'stale' })

    await useMetaStore.getState().fetchProposals()

    expect(useMetaStore.getState().error).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets error state on API failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/meta/proposals', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    await useMetaStore.getState().fetchProposals()

    expect(useMetaStore.getState().error).toBe('boom')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})

describe('fetchSignals', () => {
  it('stores signals and clears error on success', async () => {
    const response = { enabled: true, domains: [] as unknown[] }
    server.use(
      http.get('/api/v1/meta/signals', () =>
        HttpResponse.json(apiSuccess(response)),
      ),
    )
    useMetaStore.setState({ error: 'stale' })

    await useMetaStore.getState().fetchSignals()

    expect(useMetaStore.getState().error).toBeNull()
    expect(useMetaStore.getState().signals).toEqual(response)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets error state on API failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/meta/signals', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    await useMetaStore.getState().fetchSignals()

    expect(useMetaStore.getState().error).toBe('boom')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})

describe('sendChat', () => {
  it('returns the response on success', async () => {
    const response = { answer: 'hi', sources: [], confidence: 0.9 }
    const requestBodies: unknown[] = []
    server.use(
      http.post('/api/v1/meta/chat', async ({ request }) => {
        requestBodies.push(await request.json())
        return HttpResponse.json(apiSuccess(response))
      }),
    )

    const result = await useMetaStore.getState().sendChat('hello')

    expect(result).toEqual(response)
    expect(useMetaStore.getState().chatLoading).toBe(false)
    expect(requestBodies[0]).toEqual({ question: 'hello' })
  })

  it('returns null, sets error state, and emits an error toast on API failure', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    const result = await useMetaStore.getState().sendChat('hello')

    expect(result).toBeNull()
    const state = useMetaStore.getState()
    expect(state.chatLoading).toBe(false)
    expect(state.error).toBe('boom')
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Chat request failed')
    expect(toasts[0]!.description).toBe('boom')
  })

  it('clears chatLoading after success and failure', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(apiSuccess({ answer: 'ok', sources: [], confidence: 1 })),
      ),
    )
    await useMetaStore.getState().sendChat('q')
    expect(useMetaStore.getState().chatLoading).toBe(false)

    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(apiError('nope')),
      ),
    )
    await useMetaStore.getState().sendChat('q')
    expect(useMetaStore.getState().chatLoading).toBe(false)
  })
})

describe('proposeConversation', () => {
  it('returns the routed result and forwards the conversation id', async () => {
    const requestBodies: unknown[] = []
    server.use(
      http.post('/api/v1/meta/chat/propose', async ({ request }) => {
        requestBodies.push(await request.json())
        return HttpResponse.json(
          apiSuccess({
            conversation_id: 'conv-1',
            status: 'needs_clarification',
            clarifying_question: 'Which quarter?',
            conversation_closed: false,
            proposals: [],
            responder_role: 'CFO',
            responder_name: 'Casey',
            routed_topic: 'budget',
            routing_confidence: 0.9,
          }),
        )
      }),
    )

    const result = await useMetaStore
      .getState()
      .proposeConversation('cut cloud budget', 'conv-1')

    expect(result?.responder_role).toBe('CFO')
    expect(result?.routed_topic).toBe('budget')
    expect(useMetaStore.getState().proposeLoading).toBe(false)
    expect(requestBodies[0]).toEqual({
      message: 'cut cloud budget',
      conversation_id: 'conv-1',
      project: null,
    })
  })

  it('returns null, sets error, and emits an error toast on API failure', async () => {
    server.use(
      http.post('/api/v1/meta/chat/propose', () =>
        HttpResponse.json(apiError('nope')),
      ),
    )

    const result = await useMetaStore.getState().proposeConversation('do a thing')

    expect(result).toBeNull()
    const state = useMetaStore.getState()
    expect(state.proposeLoading).toBe(false)
    expect(state.error).toBe('nope')
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.title).toBe('Propose request failed')
  })

  it('shows a distinct "unavailable" toast with the backend reason on 503', async () => {
    server.use(
      http.post('/api/v1/meta/chat/propose', () =>
        HttpResponse.json(
          serviceUnavailable('Clarify-and-propose is not enabled.'),
          { status: 503 },
        ),
      ),
    )

    const result = await useMetaStore.getState().proposeConversation('do a thing')

    expect(result).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.title).toBe('Conversational mode unavailable')
    expect(toasts[0]!.description).toBe('Clarify-and-propose is not enabled.')
  })
})

describe('fetchActiveAgents', () => {
  it('stores the active roster and clears error on success', async () => {
    const roster = [{ id: 'a-1', name: 'Dana', role: 'CEO' }]
    server.use(
      http.get('/api/v1/agents/active', () =>
        HttpResponse.json(apiSuccess(roster)),
      ),
    )
    useMetaStore.setState({ error: 'stale' })

    await useMetaStore.getState().fetchActiveAgents()

    expect(useMetaStore.getState().activeAgents).toEqual(roster)
    expect(useMetaStore.getState().error).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets error state on API failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/agents/active', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    await useMetaStore.getState().fetchActiveAgents()

    expect(useMetaStore.getState().error).toBe('boom')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})

describe('converseGroup', () => {
  it('returns the round result and forwards message + participant ids', async () => {
    const requestBodies: unknown[] = []
    server.use(
      http.post('/api/v1/meta/chat/group', async ({ request }) => {
        requestBodies.push(await request.json())
        return HttpResponse.json(
          apiSuccess({
            conversation_id: 'conv-grp-1',
            contributions: [],
            participants: [],
            participants_skipped: [],
            truncated_reason: null,
          }),
        )
      }),
    )

    const result = await useMetaStore
      .getState()
      .converseGroup('kick off', ['a-1', 'a-2'])

    expect(result?.conversation_id).toBe('conv-grp-1')
    expect(useMetaStore.getState().groupChatLoading).toBe(false)
    expect(requestBodies[0]).toEqual({
      message: 'kick off',
      conversation_id: null,
      participants: ['a-1', 'a-2'],
    })
  })

  it('returns null, sets error, and emits an error toast on API failure', async () => {
    server.use(
      http.post('/api/v1/meta/chat/group', () =>
        HttpResponse.json(apiError('nope')),
      ),
    )

    const result = await useMetaStore
      .getState()
      .converseGroup('hi', ['a-1'])

    expect(result).toBeNull()
    const state = useMetaStore.getState()
    expect(state.groupChatLoading).toBe(false)
    expect(state.error).toBe('nope')
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.title).toBe('Group chat request failed')
  })

  it('shows a distinct "unavailable" toast with the backend reason on 503', async () => {
    server.use(
      http.post('/api/v1/meta/chat/group', () =>
        HttpResponse.json(serviceUnavailable('Group chat is not enabled.'), {
          status: 503,
        }),
      ),
    )

    const result = await useMetaStore.getState().converseGroup('hi', ['a-1'])

    expect(result).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.title).toBe('Conversational mode unavailable')
    expect(toasts[0]!.description).toBe('Group chat is not enabled.')
  })
})

describe('runAction', () => {
  it('returns the action result and forwards instruction + agent + id', async () => {
    const requestBodies: unknown[] = []
    server.use(
      http.post('/api/v1/meta/chat/act', async ({ request }) => {
        requestBodies.push(await request.json())
        return HttpResponse.json(
          apiSuccess({
            agent_id: 'agent-cfo',
            agent_name: 'Casey',
            conversation_id: 'conv-act-1',
            action: {
              termination_reason: 'completed',
              final_message: 'Done.',
              tool_calls: [],
              approval_id: null,
              parked: false,
            },
          }),
        )
      }),
    )

    const result = await useMetaStore
      .getState()
      .runAction('check revenue', 'agent-cfo', 'conv-act-1')

    expect(result?.agent_name).toBe('Casey')
    expect(useMetaStore.getState().actionLoading).toBe(false)
    expect(requestBodies[0]).toEqual({
      instruction: 'check revenue',
      agent: 'agent-cfo',
      conversation_id: 'conv-act-1',
    })
  })

  it('returns null, sets error, and emits an error toast on API failure', async () => {
    server.use(
      http.post('/api/v1/meta/chat/act', () =>
        HttpResponse.json(apiError('nope')),
      ),
    )

    const result = await useMetaStore
      .getState()
      .runAction('do a thing', 'agent-cfo')

    expect(result).toBeNull()
    const state = useMetaStore.getState()
    expect(state.actionLoading).toBe(false)
    expect(state.error).toBe('nope')
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.title).toBe('Direct action request failed')
  })

  it('shows a distinct "unavailable" toast on 503 (fail-closed governance)', async () => {
    server.use(
      http.post('/api/v1/meta/chat/act', () =>
        HttpResponse.json(
          serviceUnavailable('Direct MCP acting requires security governance.'),
          { status: 503 },
        ),
      ),
    )

    const result = await useMetaStore
      .getState()
      .runAction('do a thing', 'agent-cfo')

    expect(result).toBeNull()
    expect(useMetaStore.getState().error).toBe(
      'Direct MCP acting requires security governance.',
    )
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.title).toBe('Conversational mode unavailable')
    expect(toasts[0]!.description).toBe(
      'Direct MCP acting requires security governance.',
    )
  })
})
