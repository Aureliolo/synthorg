import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import type { postTurn } from '@/api/endpoints/meta'
import { successFor } from '@/mocks/handlers/helpers'
import { useOrgConversationStore } from '@/stores/org-conversation'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

const TURN = '/api/v1/meta/chat/turn'

function proposeTurn(closed: boolean) {
  return successFor<typeof postTurn>({
    intent: 'propose',
    intent_reason: 'classified',
    intent_confidence: 0.9,
    conversation_id: 'conv-1',
    answer: null,
    propose: {
      conversation_id: 'conv-1',
      status: closed ? 'proposed' : 'needs_clarification',
      clarifying_question: closed ? null : 'Which platform?',
      conversation_closed: closed,
      plan_draft: closed ? { task_id: 't1', project: 'P', title: 'Plan' } : null,
      responder_role: null,
      responder_name: null,
      routed_topic: null,
      routing_confidence: null,
      routing_reason: 'no_role_router',
      steering: [],
    },
    group: null,
    act: null,
    charter: null,
    chime_ins: [],
  })
}

function send(message: string, signal?: AbortSignal) {
  return useOrgConversationStore.getState().sendTurn(message, {
    idempotencyKey: 'k1',
    ...(signal && { signal }),
  })
}

beforeEach(() => {
  useOrgConversationStore.getState().resetAll()
})

describe('useOrgConversationStore', () => {
  it('appends the operator turn and the org answer for an explain turn', async () => {
    await send('how are we doing?')
    const { messages, activeIntent, conversationId } = useOrgConversationStore.getState()
    expect(messages.map((m) => m.kind)).toEqual(['human', 'assistant'])
    // Explain is stateless: it never pins a capability or a conversation.
    expect(activeIntent).toBeUndefined()
    expect(conversationId).toBeUndefined()
  })

  it('pins the capability and conversation for a stateful propose turn', async () => {
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(false))))
    await send('build a landing page')
    const state = useOrgConversationStore.getState()
    expect(state.activeIntent).toBe('propose')
    expect(state.conversationId).toBe('conv-1')
    expect(state.conversationClosed).toBe(false)
  })

  it('freezes the thread once a propose conversation closes', async () => {
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(true))))
    await send('ship it')
    expect(useOrgConversationStore.getState().conversationClosed).toBe(true)
    // A closed thread rejects further sends.
    await send('another')
    expect(
      useOrgConversationStore.getState().messages.filter((m) => m.kind === 'human'),
    ).toHaveLength(1)
  })

  it('surfaces a failure as an error notice and a toast', async () => {
    server.use(
      http.post(TURN, () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
    )
    await send('do a thing')
    const last = useOrgConversationStore.getState().messages.at(-1)
    expect(last).toMatchObject({ kind: 'notice', isError: true })
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0)
  })

  it('renders a cancelled notice on abort without a toast', async () => {
    const controller = new AbortController()
    controller.abort()
    await send('slow turn', controller.signal)
    const last = useOrgConversationStore.getState().messages.at(-1)
    expect(last).toMatchObject({ kind: 'notice' })
    expect(last).not.toMatchObject({ isError: true })
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('startNew clears the thread', async () => {
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(false))))
    await send('build a landing page')
    useOrgConversationStore.getState().startNew()
    const state = useOrgConversationStore.getState()
    expect(state.messages).toHaveLength(0)
    expect(state.conversationId).toBeUndefined()
    expect(state.activeIntent).toBeUndefined()
  })
})
