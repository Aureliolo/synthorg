import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import type { answerParkedQuestion } from '@/api/endpoints/chat-questions'
import type { WsEvent, WsEventType } from '@/api/types/websocket'
import {
  apiError,
  openQuestionsHandler,
  parkedQuestionFixture,
  questionsPage,
  successFor,
} from '@/mocks/handlers'
import { useOrgQuestionsStore } from '@/stores/org-questions'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

const ANSWER_URL = '/api/v1/meta/chat/questions/:approvalId/answer'
const DECLINE_URL = '/api/v1/meta/chat/questions/:approvalId/decline'

function hasToast(variant: 'success' | 'error'): boolean {
  return useToastStore.getState().toasts.some((t) => t.variant === variant)
}

function submittedEvent(actionType: string): WsEvent {
  return {
    event_type: 'approval.submitted',
    channel: 'approvals',
    timestamp: '2026-08-02T10:00:00Z',
    payload: { approval: { id: 'question-1', action_type: actionType } },
  }
}

describe('useOrgQuestionsStore', () => {
  it('fetchQuestions populates the list and mints a turn id per question', async () => {
    server.use(
      openQuestionsHandler([
        parkedQuestionFixture({ approval_id: 'q-1' }),
        parkedQuestionFixture({ approval_id: 'q-2' }),
      ]),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    const state = useOrgQuestionsStore.getState()
    expect(state.questions.map((r) => r.question.approval_id)).toEqual(['q-1', 'q-2'])
    expect(state.error).toBeNull()
    expect(state.loading).toBe(false)
    const [first, second] = state.questions
    expect(first?.turnId).not.toBe(second?.turnId)
  })

  it('a refetch keeps the turn id of a question that is still open', async () => {
    server.use(openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]))
    await useOrgQuestionsStore.getState().fetchQuestions()
    const before = useOrgQuestionsStore.getState().questions[0]?.turnId

    server.use(
      openQuestionsHandler([
        parkedQuestionFixture({ approval_id: 'q-1', question: 'Reworded?' }),
        parkedQuestionFixture({ approval_id: 'q-2' }),
      ]),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    const after = useOrgQuestionsStore.getState().questions
    expect(after[0]?.turnId).toBe(before)
    expect(after[1]?.turnId).not.toBe(before)
  })

  it('fetchQuestions records the error instead of toasting', async () => {
    server.use(
      http.get('/api/v1/meta/chat/questions', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    expect(useOrgQuestionsStore.getState().error).not.toBeNull()
    expect(hasToast('error')).toBe(false)
  })

  it('a question WS event refetches and a non-question one does not', async () => {
    let listCalls = 0
    server.use(
      http.get('/api/v1/meta/chat/questions', () => {
        listCalls += 1
        return HttpResponse.json(
          questionsPage([parkedQuestionFixture({ approval_id: 'q-1' })]),
        )
      }),
    )

    useOrgQuestionsStore.getState().handleWsEvent(submittedEvent('comms:external'))
    useOrgQuestionsStore.getState().handleWsEvent(submittedEvent('decision:project'))
    await vi.waitFor(() =>
      expect(useOrgQuestionsStore.getState().questions).toHaveLength(1),
    )
    expect(listCalls).toBe(1)
  })

  it('coalesces a burst of question events into two reads, not five', async () => {
    // One event proves nothing: it cannot tell coalescing from "only one fetch
    // was ever triggered". Five must produce exactly two reads -- the one in
    // flight, plus one more covering everything that arrived during it.
    let listCalls = 0
    let release = (): void => {}
    const firstResponseSent = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.get('/api/v1/meta/chat/questions', async () => {
        listCalls += 1
        if (listCalls === 1) await firstResponseSent
        return HttpResponse.json(
          questionsPage([parkedQuestionFixture({ approval_id: 'q-1' })]),
        )
      }),
    )

    const store = useOrgQuestionsStore.getState()
    store.handleWsEvent(submittedEvent('clarify:question'))
    await vi.waitFor(() => expect(listCalls).toBe(1))
    for (let i = 0; i < 4; i++) {
      store.handleWsEvent(submittedEvent('clarify:question'))
    }
    release()

    await vi.waitFor(() => expect(listCalls).toBe(2))
    await vi.waitFor(() =>
      expect(useOrgQuestionsStore.getState().loading).toBe(false),
    )
    expect(listCalls).toBe(2)
  })

  it('an answer echoes what the server recorded when it differs', async () => {
    // On a decision the server resolves the chosen option's writeup, so the
    // recorded text is not what the operator typed and is the only place they
    // see what the agent actually received.
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]),
      http.post(ANSWER_URL, () =>
        HttpResponse.json(
          successFor<typeof answerParkedQuestion>({
            approval_id: 'q-1',
            status: 'approved',
            recorded_answer: 'SQLite: zero ops, single writer.',
            decided_at: '2026-08-02T10:05:00Z',
          }),
        ),
      ),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    await useOrgQuestionsStore.getState().answerQuestion('q-1', 'SQLite', 'sqlite')

    const toast = useToastStore.getState().toasts.at(-1)
    expect(toast?.description).toContain('SQLite: zero ops, single writer.')
  })

  it('adds no echo when the recorded answer is what was typed', async () => {
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]),
      http.post(ANSWER_URL, () =>
        HttpResponse.json(
          successFor<typeof answerParkedQuestion>({
            approval_id: 'q-1',
            status: 'approved',
            recorded_answer: 'Use Postgres.',
            decided_at: '2026-08-02T10:05:00Z',
          }),
        ),
      ),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    await useOrgQuestionsStore.getState().answerQuestion('q-1', 'Use Postgres.')

    expect(useToastStore.getState().toasts.at(-1)?.description).toBeUndefined()
  })

  it('reuses one idempotency key across a retry of the same answer', async () => {
    // A user-driven retry after a timeout has to carry the SAME key, or the
    // server cannot recognise the replay: it re-decides, 409s, and the
    // operator is told their answer failed when it landed.
    const keys: (string | null)[] = []
    let attempt = 0
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]),
      http.post(ANSWER_URL, ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        attempt += 1
        if (attempt === 1) {
          return HttpResponse.json(apiError('gateway timeout'), { status: 504 })
        }
        return HttpResponse.json(
          successFor<typeof answerParkedQuestion>({
            approval_id: 'q-1',
            status: 'approved',
            recorded_answer: 'Postgres',
            decided_at: '2026-08-02T10:05:00Z',
          }),
        )
      }),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    expect(await useOrgQuestionsStore.getState().answerQuestion('q-1', 'Postgres')).toBe(
      false,
    )
    expect(await useOrgQuestionsStore.getState().answerQuestion('q-1', 'Postgres')).toBe(
      true,
    )

    expect(keys).toHaveLength(2)
    expect(keys[0]).toBe(keys[1])
  })

  it.each<WsEventType>([
    'approval.approved',
    'approval.rejected',
    'approval.expired',
  ])(
    '%s removes the question locally without a refetch',
    async (eventType) => {
      server.use(openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]))
      await useOrgQuestionsStore.getState().fetchQuestions()

      useOrgQuestionsStore.getState().handleWsEvent({
        event_type: eventType,
        channel: 'approvals',
        timestamp: '2026-08-02T10:01:00Z',
        payload: { approval: { id: 'q-1', action_type: 'clarify:question' } },
      })

      expect(useOrgQuestionsStore.getState().questions).toEqual([])
    },
  )

  it('a decided event falls back to the flat approval_id payload', async () => {
    server.use(openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]))
    await useOrgQuestionsStore.getState().fetchQuestions()

    useOrgQuestionsStore.getState().handleWsEvent({
      event_type: 'approval.approved',
      channel: 'approvals',
      timestamp: '2026-08-02T10:01:00Z',
      payload: { approval_id: 'q-1' },
    })

    expect(useOrgQuestionsStore.getState().questions).toEqual([])
  })

  it.each<{ name: string; payload: Record<string, unknown> }>([
    { name: 'an empty payload', payload: {} },
    { name: 'a non-object approval', payload: { approval: 'q-1' } },
    { name: 'a non-string id', payload: { approval: { id: 42 } } },
  ])('drops $name without throwing or clearing the list', async ({ payload }) => {
    server.use(openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]))
    await useOrgQuestionsStore.getState().fetchQuestions()

    expect(() => {
      useOrgQuestionsStore.getState().handleWsEvent({
        event_type: 'approval.approved',
        channel: 'approvals',
        timestamp: '2026-08-02T10:01:00Z',
        payload,
      })
      useOrgQuestionsStore.getState().handleWsEvent({
        event_type: 'approval.submitted',
        channel: 'approvals',
        timestamp: '2026-08-02T10:01:00Z',
        payload,
      })
    }).not.toThrow()
    expect(useOrgQuestionsStore.getState().questions).toHaveLength(1)
  })

  it('answerQuestion removes the card and toasts success', async () => {
    server.use(openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]))
    await useOrgQuestionsStore.getState().fetchQuestions()

    const ok = await useOrgQuestionsStore
      .getState()
      .answerQuestion('q-1', 'Use Postgres.')

    expect(ok).toBe(true)
    expect(useOrgQuestionsStore.getState().questions).toEqual([])
    expect(useOrgQuestionsStore.getState().resolving.size).toBe(0)
    expect(hasToast('success')).toBe(true)
  })

  it('answerQuestion returns false, toasts and keeps the card on failure', async () => {
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]),
      http.post(ANSWER_URL, () =>
        HttpResponse.json(apiError('already decided'), { status: 409 }),
      ),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    const ok = await useOrgQuestionsStore.getState().answerQuestion('q-1', 'Postgres')

    expect(ok).toBe(false)
    expect(useOrgQuestionsStore.getState().questions).toHaveLength(1)
    expect(useOrgQuestionsStore.getState().resolving.size).toBe(0)
    expect(hasToast('error')).toBe(true)
  })

  it('declineQuestion removes the card and toasts success', async () => {
    server.use(openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]))
    await useOrgQuestionsStore.getState().fetchQuestions()

    const ok = await useOrgQuestionsStore.getState().declineQuestion('q-1')

    expect(ok).toBe(true)
    expect(useOrgQuestionsStore.getState().questions).toEqual([])
    expect(hasToast('success')).toBe(true)
  })

  it('declineQuestion returns false and keeps the card on failure', async () => {
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ approval_id: 'q-1' })]),
      http.post(DECLINE_URL, () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    await useOrgQuestionsStore.getState().fetchQuestions()

    expect(await useOrgQuestionsStore.getState().declineQuestion('q-1')).toBe(false)
    expect(useOrgQuestionsStore.getState().questions).toHaveLength(1)
    expect(hasToast('error')).toBe(true)
  })
})
