import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import type { postTurn } from '@/api/endpoints/meta'
import {
  deferredStreamBody,
  explainStreamBody,
  sseFrame,
  sseResponse,
} from '@/mocks/handlers'
import { successFor } from '@/mocks/handlers/helpers'
import { useOrgConversationStore } from '@/stores/org-conversation'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

const TURN = '/api/v1/meta/chat/turn'
const TURN_STREAM = '/api/v1/meta/chat/turn/stream'

/** Defer the stream to `intent` so the buffered endpoint runs the capability. */
function deferStreamTo(intent: 'propose' | 'group_convene' | 'act' | 'charter') {
  server.use(http.post(TURN_STREAM, () => sseResponse(deferredStreamBody(intent))))
}

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
      responder_role: null,
      responder_name: null,
      routed_topic: null,
      routing_confidence: null,
      routing_reason: 'no_role_router',
      steering: closed
        ? [{ text: 'Use Postgres', approval_id: 'a1', kind: 'redirect', project: 'P' }]
        : [],
    },
    group: null,
    act: null,
    configure: null,
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
  it('streams the org answer for an explain turn into an assistant bubble', async () => {
    // The default stream handler emits two deltas then a complete frame.
    await send('how are we doing?')
    const { messages, activeIntent, conversationId } = useOrgConversationStore.getState()
    expect(messages.map((m) => m.kind)).toEqual(['human', 'assistant'])
    const answer = messages.at(-1)
    expect(answer).toMatchObject({
      kind: 'assistant',
      content: 'The organisation is healthy.',
    })
    // The bubble is finalised (not left streaming) once the complete frame lands.
    expect(answer).not.toMatchObject({ isStreaming: true })
    // Explain is stateless: it never pins a capability or a conversation.
    expect(activeIntent).toBeUndefined()
    expect(conversationId).toBeUndefined()
  })

  it('renders a specialist chime-in streamed after the answer', async () => {
    server.use(
      http.post(TURN_STREAM, () =>
        sseResponse(
          explainStreamBody('Runway is fine.') +
            sseFrame('chime', { role: 'CFO', name: 'Casey', content: 'Watch Q3.' }),
        ),
      ),
    )
    await send('how is our runway?')
    const kinds = useOrgConversationStore.getState().messages.map((m) => m.kind)
    expect(kinds).toEqual(['human', 'assistant', 'agent'])
  })

  it('appends a notice when a streamed explain is a silent intent degrade', async () => {
    const complete = {
      intent: 'explain',
      intent_reason: 'act_no_target',
      intent_confidence: null,
      conversation_id: null,
      answer: { answer: "Here's what I'd do.", sources: [], cited_records: [], confidence: 0.6 },
      propose: null,
      group: null,
      act: null,
      configure: null,
      charter: null,
      chime_ins: [],
    }
    server.use(
      http.post(TURN_STREAM, () =>
        sseResponse(sseFrame('delta', { delta: 'x' }) + sseFrame('complete', complete)),
      ),
    )
    await send('delete the ticket now')
    const last = useOrgConversationStore.getState().messages.at(-1)
    expect(last).toMatchObject({ kind: 'notice' })
  })

  it('pins the capability and conversation for a deferred propose turn', async () => {
    // A non-explain turn defers from the stream and runs on the buffered POST.
    deferStreamTo('propose')
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(false))))
    await send('build a landing page')
    const state = useOrgConversationStore.getState()
    expect(state.activeIntent).toBe('propose')
    expect(state.conversationId).toBe('conv-1')
    expect(state.conversationClosed).toBe(false)
  })

  it('freezes the thread once a propose conversation closes', async () => {
    deferStreamTo('propose')
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(true))))
    await send('ship it')
    expect(useOrgConversationStore.getState().conversationClosed).toBe(true)
    // A closed thread rejects further sends.
    await send('another')
    expect(
      useOrgConversationStore.getState().messages.filter((m) => m.kind === 'human'),
    ).toHaveLength(1)
  })

  it('surfaces a stream failure as an error notice and a toast', async () => {
    server.use(
      http.post(TURN_STREAM, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
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
    deferStreamTo('propose')
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(false))))
    await send('build a landing page')
    useOrgConversationStore.getState().startNew()
    const state = useOrgConversationStore.getState()
    expect(state.messages).toHaveLength(0)
    expect(state.conversationId).toBeUndefined()
    expect(state.activeIntent).toBeUndefined()
  })

  it('an impatient second send never dispatches a second turn', async () => {
    // The turn that prompted this took 14.5 seconds. A re-send while the first
    // is still running would fork a second initiative over one objective, so
    // the guard is the store's, not the button's: a disabled control is a hint,
    // and the dispatch has to refuse regardless.
    deferStreamTo('propose')
    let dispatches = 0
    server.use(
      http.post(TURN, () => {
        dispatches += 1
        return HttpResponse.json(proposeTurn(false))
      }),
    )

    const first = send('build a landing page')
    await send('build a landing page')
    await first

    expect(dispatches).toBe(1)
    expect(
      useOrgConversationStore.getState().messages.filter((m) => m.kind === 'human'),
    ).toHaveLength(1)
  })

  it('keeps the key a turn was sent with, so a retry cannot double-dispatch', async () => {
    // The retry path replays the key off the human turn: minting a fresh one
    // would re-run an ACT turn's tools against a request the server already
    // accepted.
    deferStreamTo('propose')
    server.use(http.post(TURN, () => HttpResponse.json(proposeTurn(false))))
    await send('build a landing page')

    const human = useOrgConversationStore
      .getState()
      .messages.find((m) => m.kind === 'human')
    expect(human).toMatchObject({ idempotencyKey: 'k1' })
  })
})
