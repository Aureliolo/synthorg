import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useMetaStore } from '@/stores/meta'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'

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
})
