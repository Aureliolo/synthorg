import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { useCharterStore } from '@/stores/charter'
import { useToastStore } from '@/stores/toast'
import { apiError, buildCharter, successFor } from '@/mocks/handlers'
import type {
  approveCharter as approveCharterApi,
  listCharters as listChartersApi,
  runInterviewTurn as runInterviewTurnApi,
} from '@/api/endpoints/charter'
import type { CharterApprovalResult, InterviewTurnResult } from '@/api/types'
import { server } from '@/test-setup'

describe('useCharterStore', () => {
  beforeEach(() => {
    useCharterStore.setState({
      charters: [],
      loading: false,
      error: null,
      conversationId: null,
      messages: [],
      draftCharter: null,
      sending: false,
      conversationClosed: false,
    })
    useToastStore.getState().dismissAll()
  })

  it('fetchCharters populates the list', async () => {
    server.use(
      http.get('/api/v1/meta/charters', () =>
        HttpResponse.json(
          successFor<typeof listChartersApi>([buildCharter({ id: 'c-1' })]),
        ),
      ),
    )
    await useCharterStore.getState().fetchCharters()
    expect(useCharterStore.getState().charters.map((c) => c.id)).toEqual(['c-1'])
    expect(useCharterStore.getState().error).toBeNull()
  })

  it('fetchCharters sets error on failure', async () => {
    server.use(
      http.get('/api/v1/meta/charters', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    await useCharterStore.getState().fetchCharters()
    expect(useCharterStore.getState().error).not.toBeNull()
  })

  it('runTurn records a clarifying question and stays open', async () => {
    const result: InterviewTurnResult = {
      conversation_id: 'conv-9',
      status: 'needs_more',
      next_question: 'What is the budget?',
      charter: null,
      conversation_closed: false,
    }
    server.use(
      http.post('/api/v1/meta/charters/interview', () =>
        HttpResponse.json(successFor<typeof runInterviewTurnApi>(result)),
      ),
    )
    await useCharterStore.getState().runTurn('build a memory tool')
    const state = useCharterStore.getState()
    expect(state.conversationId).toBe('conv-9')
    expect(state.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(state.messages[1]!.content).toBe('What is the budget?')
    expect(state.draftCharter).toBeNull()
  })

  it('runTurn captures a drafted charter', async () => {
    // Default MSW handler returns a drafted charter.
    await useCharterStore.getState().runTurn('a clear idea')
    expect(useCharterStore.getState().draftCharter?.status).toBe('drafted')
  })

  it('runTurn toasts on failure and clears sending', async () => {
    server.use(
      http.post('/api/v1/meta/charters/interview', () =>
        HttpResponse.json(apiError('nope'), { status: 502 }),
      ),
    )
    await useCharterStore.getState().runTurn('idea')
    expect(useCharterStore.getState().sending).toBe(false)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
  })

  it('approve returns the result and emits a success toast', async () => {
    const charter = buildCharter({
      id: 'c-1',
      status: 'approved',
      approved_by: 'op',
      approved_at: '2026-05-22T00:00:00Z',
      forecast_id: 'f-1',
      correlation_id: 'conv-1',
      task_id: 'task-1',
    })
    const result: CharterApprovalResult = {
      charter,
      project_id: 'charter-c-1',
      task_id: 'task-1',
      is_success: true,
    }
    server.use(
      http.post('/api/v1/meta/charters/:id/approve', () =>
        HttpResponse.json(successFor<typeof approveCharterApi>(result)),
      ),
    )
    const out = await useCharterStore.getState().approve('c-1')
    expect(out?.task_id).toBe('task-1')
    expect(useCharterStore.getState().draftCharter?.status).toBe('approved')
    expect(useToastStore.getState().toasts[0]!.variant).toBe('success')
  })

  it('cancel transitions the draft to cancelled', async () => {
    const ok = await useCharterStore.getState().cancel('charter-default')
    expect(ok).toBe(true)
    expect(useCharterStore.getState().draftCharter?.status).toBe('cancelled')
  })

  it('editDraft updates the draft', async () => {
    const updated = await useCharterStore
      .getState()
      .editDraft('charter-default', { brief: 'sharper' })
    expect(updated?.version).toBe(2)
  })

  it('resetInterview clears the active interview', () => {
    useCharterStore.setState({
      conversationId: 'x',
      messages: [{ id: 'm', role: 'user', content: 'hi' }],
      draftCharter: buildCharter(),
      conversationClosed: true,
    })
    useCharterStore.getState().resetInterview()
    const state = useCharterStore.getState()
    expect(state.conversationId).toBeNull()
    expect(state.messages).toEqual([])
    expect(state.draftCharter).toBeNull()
    expect(state.conversationClosed).toBe(false)
  })
})
