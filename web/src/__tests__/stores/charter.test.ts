import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { useCharterStore } from '@/stores/charter'
import { useToastStore } from '@/stores/toast'
import { apiError, buildCharter, paginatedFor, successFor } from '@/mocks/handlers'
import type {
  approveCharter as approveCharterApi,
  editCharter as editCharterApi,
  listCharters as listChartersApi,
} from '@/api/endpoints/charter'
import type { CharterApprovalResult } from '@/api/types'
import { server } from '@/test-setup'

describe('useCharterStore', () => {
  beforeEach(() => {
    useCharterStore.setState({
      charters: [],
      loading: false,
      error: null,
      nextCursor: null,
      hasMore: false,
      draftCharter: null,
      mutating: false,
    })
    useToastStore.getState().dismissAll()
  })

  it('fetchCharters populates the list', async () => {
    const data = [buildCharter({ id: 'c-1' })]
    server.use(
      http.get('/api/v1/meta/charters', () =>
        HttpResponse.json(
          paginatedFor<typeof listChartersApi>({
            data,
            limit: 50,
            nextCursor: null,
            hasMore: false,
            pagination: { limit: 50, next_cursor: null, has_more: false },
          }),
        ),
      ),
    )
    await useCharterStore.getState().fetchCharters()
    expect(useCharterStore.getState().charters.map((c) => c.id)).toEqual(['c-1'])
    expect(useCharterStore.getState().error).toBeNull()
    expect(useCharterStore.getState().hasMore).toBe(false)
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

  it('hydrateFromTurn adopts a drafted charter for the side panel', () => {
    useCharterStore.getState().hydrateFromTurn({
      conversation_id: 'conv-9',
      status: 'drafted',
      next_question: null,
      charter: buildCharter({ id: 'c-hydrated' }),
      conversation_closed: false,
    })
    expect(useCharterStore.getState().draftCharter?.id).toBe('c-hydrated')
  })

  it('hydrateFromTurn keeps the prior draft when a turn carries none', () => {
    useCharterStore.setState({ draftCharter: buildCharter({ id: 'c-keep' }) })
    useCharterStore.getState().hydrateFromTurn({
      conversation_id: 'conv-9',
      status: 'needs_more',
      next_question: 'What is the budget?',
      charter: null,
      conversation_closed: false,
    })
    expect(useCharterStore.getState().draftCharter?.id).toBe('c-keep')
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

  it('approve surfaces an error toast when the run failed (is_success false)', async () => {
    const charter = buildCharter({
      id: 'c-1',
      status: 'approved',
      approved_by: 'op',
      approved_at: '2026-05-22T00:00:00Z',
      correlation_id: 'conv-1',
      task_id: 'task-1',
    })
    const result: CharterApprovalResult = {
      charter,
      project_id: 'charter-c-1',
      task_id: 'task-1',
      is_success: false,
    }
    server.use(
      http.post('/api/v1/meta/charters/:id/approve', () =>
        HttpResponse.json(successFor<typeof approveCharterApi>(result)),
      ),
    )
    // The charter was approved (a human decided) but the run produced no
    // successful work: the store must surface that as an error, not a false
    // success, so the operator opens Plan Review for the FAILED plan.
    const out = await useCharterStore.getState().approve('c-1')
    expect(out?.is_success).toBe(false)
    const toast = useToastStore.getState().toasts[0]!
    expect(toast.variant).toBe('error')
    expect(toast.title).toMatch(/run failed/i)
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

  it('resetInterview clears the active draft', () => {
    useCharterStore.setState({ draftCharter: buildCharter(), mutating: true })
    useCharterStore.getState().resetInterview()
    const state = useCharterStore.getState()
    expect(state.draftCharter).toBeNull()
    expect(state.mutating).toBe(false)
  })

  it('drops a stale edit completion so it cannot repopulate a reset draft', async () => {
    // An edit is in flight against the current draft; the operator resets the
    // interview before it resolves. The late completion must be discarded: it
    // must NOT re-open the (now cleared) draft panel, and must not flip the
    // reset-state `mutating` flag back on.
    let releaseEdit: (() => void) | undefined
    const editGate = new Promise<void>((resolve) => {
      releaseEdit = resolve
    })
    // Signal when the PATCH handler is actually entered, so the test resets and
    // releases only once the request is genuinely parked -- otherwise the reset
    // could race ahead of the in-flight edit it is meant to interleave with.
    let markRequestStarted: (() => void) | undefined
    const requestStarted = new Promise<void>((resolve) => {
      markRequestStarted = resolve
    })
    server.use(
      http.patch('/api/v1/meta/charters/:id', async () => {
        markRequestStarted?.()
        await editGate
        return HttpResponse.json(
          successFor<typeof editCharterApi>(
            buildCharter({ id: 'c-stale', version: 9 }),
          ),
        )
      }),
    )
    useCharterStore.setState({ draftCharter: buildCharter({ id: 'c-stale' }) })
    const editing = useCharterStore.getState().editDraft('c-stale', { brief: 'x' })
    await requestStarted
    // Reset while the PATCH is still parked, bumping the draft generation.
    useCharterStore.getState().resetInterview()
    if (releaseEdit === undefined) throw new Error('edit release callback missing')
    releaseEdit()
    await editing
    const state = useCharterStore.getState()
    expect(state.draftCharter).toBeNull()
    expect(state.mutating).toBe(false)
  })
})
