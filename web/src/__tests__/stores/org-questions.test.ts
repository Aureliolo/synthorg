import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import type { listParkedQuestions } from '@/api/endpoints/chat-questions'
import type { WsEvent, WsEventType } from '@/api/types/websocket'
import { apiError, openQuestionsHandler, parkedQuestionFixture } from '@/mocks/handlers'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
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
          paginatedEnvelopeFor<typeof listParkedQuestions>([
            parkedQuestionFixture({ approval_id: 'q-1' }),
          ]),
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
