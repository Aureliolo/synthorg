import fc from 'fast-check'
import { http, HttpResponse } from 'msw'
import { useArtifactsStore } from '@/stores/artifacts'
import { makeArtifact } from '../helpers/factories'
import { apiError, apiSuccess, paginatedFor, voidSuccess } from '@/mocks/handlers'
import type { listArtifacts } from '@/api/endpoints/artifacts'
import { server } from '@/test-setup'
import type { Artifact } from '@/api/types/artifacts'
import type { WsEvent } from '@/api/types/websocket'

function paginated(
  data: Artifact[],
  meta: Partial<{ total: number; offset: number; limit: number }> = {},
) {
  const limit = meta.limit ?? 200
  return paginatedFor<typeof listArtifacts>({
    data,
    limit,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit,
      next_cursor: null,
      has_more: false,
    },
  })
}

describe('useArtifactsStore', () => {
  beforeEach(() => {
    useArtifactsStore.setState({
      artifacts: [],
      totalArtifacts: 0,
      listLoading: false,
      listError: null,
      searchQuery: '',
      typeFilter: null,
      createdByFilter: null,
      taskIdFilter: null,
      contentTypeFilter: null,
      projectIdFilter: null,
      selectedArtifact: null,
      contentPreview: null,
      detailLoading: false,
      detailError: null,
    })
  })

  describe('fetchArtifacts', () => {
    it('populates artifacts on success', async () => {
      const artifact = makeArtifact('artifact-001')
      server.use(
        http.get('/api/v1/artifacts', () =>
          HttpResponse.json(paginated([artifact], { total: 1 })),
        ),
      )

      await useArtifactsStore.getState().fetchArtifacts()

      const state = useArtifactsStore.getState()
      expect(state.artifacts).toEqual([artifact])
      expect(state.totalArtifacts).toBe(1)
      expect(state.listLoading).toBe(false)
    })

    it('sets error on failure', async () => {
      server.use(
        http.get('/api/v1/artifacts', () =>
          HttpResponse.json(apiError('Network error')),
        ),
      )

      await useArtifactsStore.getState().fetchArtifacts()

      expect(useArtifactsStore.getState().listError).toBe('Network error')
    })
  })

  describe('fetchArtifactDetail', () => {
    it('populates selected artifact', async () => {
      const artifact = makeArtifact('artifact-001', {
        content_type: 'text/plain',
        size_bytes: 100,
      })
      server.use(
        http.get('/api/v1/artifacts/:id', () =>
          HttpResponse.json(apiSuccess(artifact)),
        ),
        http.get('/api/v1/artifacts/:id/content', () =>
          new HttpResponse('hello world', {
            headers: { 'Content-Type': 'text/plain' },
          }),
        ),
      )

      await useArtifactsStore.getState().fetchArtifactDetail('artifact-001')

      const state = useArtifactsStore.getState()
      expect(state.selectedArtifact).toEqual(artifact)
      expect(state.contentPreview).toBe('hello world')
    })

    it('sets error when artifact not found', async () => {
      server.use(
        http.get('/api/v1/artifacts/:id', () =>
          HttpResponse.json(apiError('Not found')),
        ),
      )

      await useArtifactsStore.getState().fetchArtifactDetail('missing')

      expect(useArtifactsStore.getState().detailError).toBe('Not found')
    })

    it('handles partial content preview failure gracefully', async () => {
      const artifact = makeArtifact('artifact-001', {
        content_type: 'text/plain',
        size_bytes: 100,
      })
      server.use(
        http.get('/api/v1/artifacts/:id', () =>
          HttpResponse.json(apiSuccess(artifact)),
        ),
        http.get('/api/v1/artifacts/:id/content', () =>
          new HttpResponse('boom', { status: 500 }),
        ),
      )

      await useArtifactsStore.getState().fetchArtifactDetail('artifact-001')

      const state = useArtifactsStore.getState()
      expect(state.selectedArtifact).toEqual(artifact)
      expect(state.contentPreview).toBeNull()
      // Restructured copy: title-style "Preview failed to load" prefix
       // with the metadata-still-shown reassurance. The user-facing
       // wording is now "Preview" rather than "content preview".
       expect(state.detailError).toMatch(/Preview failed to load/)
    })
  })

  describe('createArtifact', () => {
    it('prepends new artifact and emits success toast', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      const existing = makeArtifact('artifact-existing')
      useArtifactsStore.setState({ artifacts: [existing], totalArtifacts: 1 })
      const created = makeArtifact('artifact-new', { path: 'src/new.py' })
      server.use(
        http.post('/api/v1/artifacts', () =>
          HttpResponse.json(apiSuccess(created)),
        ),
      )

      const result = await useArtifactsStore.getState().createArtifact({
        type: 'code',
        path: 'src/new.py',
        task_id: 'task-1',
        created_by: 'alice',
        description: '',
        content_type: '',
      })

      expect(result).toEqual(created)
      const state = useArtifactsStore.getState()
      expect(state.artifacts).toEqual([created, existing])
      expect(state.totalArtifacts).toBe(2)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('success')
      expect(toasts[0]!.title).toBe('Artifact created')
    })

    it('returns null sentinel + emits error toast on failure', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      const existing = makeArtifact('artifact-existing')
      useArtifactsStore.setState({ artifacts: [existing], totalArtifacts: 1 })
      server.use(
        http.post('/api/v1/artifacts', () =>
          HttpResponse.json(apiError('Quota exceeded'), { status: 422 }),
        ),
      )

      const result = await useArtifactsStore.getState().createArtifact({
        type: 'code',
        path: 'src/new.py',
        task_id: 'task-1',
        created_by: 'alice',
        description: '',
        content_type: '',
      })

      expect(result).toBeNull()
      const state = useArtifactsStore.getState()
      // List unchanged on failure -- no optimistic insert to roll back.
      expect(state.artifacts).toEqual([existing])
      expect(state.totalArtifacts).toBe(1)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to create artifact')
      expect(toasts[0]!.description).toContain('Quota exceeded')
    })

    it('clears listLoading when superseding an in-flight fetchArtifacts', async () => {
      // Regression: createArtifact bumps _listRequestToken so any
      // concurrent fetchArtifacts bails on its stale check. Without an
      // explicit listLoading reset the page would stay stuck on the
      // skeleton until a manual reload.
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      let resolveFetch: (() => void) | null = null
      const fetchGate = new Promise<void>((resolve) => {
        resolveFetch = resolve
      })
      server.use(
        http.get('/api/v1/artifacts', async () => {
          await fetchGate
          return HttpResponse.json(apiSuccess({ data: [], pagination: null }))
        }),
        http.post('/api/v1/artifacts', () =>
          HttpResponse.json(apiSuccess(makeArtifact('artifact-new'))),
        ),
      )

      const fetchPromise = useArtifactsStore.getState().fetchArtifacts()
      // listLoading flips to true synchronously inside fetchArtifacts.
      expect(useArtifactsStore.getState().listLoading).toBe(true)

      await useArtifactsStore.getState().createArtifact({
        type: 'code',
        path: 'src/new.py',
        task_id: 'task-1',
        created_by: 'alice',
        description: '',
        content_type: '',
      })

      // Even before the stale fetch settles, the create has cleared the
      // skeleton so the user sees the optimistic insert.
      expect(useArtifactsStore.getState().listLoading).toBe(false)

      resolveFetch!()
      await fetchPromise

      // Final state still reflects the optimistic insert; the stale
      // fetch's empty payload was discarded by the token bump.
      const after = useArtifactsStore.getState()
      expect(after.listLoading).toBe(false)
      expect(after.artifacts.map((a) => a.id)).toContain('artifact-new')
    })
  })

  describe('deleteArtifact', () => {
    it('removes artifact from list', async () => {
      const a1 = makeArtifact('artifact-001')
      const a2 = makeArtifact('artifact-002')
      useArtifactsStore.setState({ artifacts: [a1, a2], totalArtifacts: 2 })
      server.use(
        http.delete('/api/v1/artifacts/:id', () =>
          HttpResponse.json(voidSuccess()),
        ),
      )

      await useArtifactsStore.getState().deleteArtifact('artifact-001')

      expect(useArtifactsStore.getState().artifacts).toEqual([a2])
      expect(useArtifactsStore.getState().totalArtifacts).toBe(1)
    })

    it('returns false sentinel + emits error toast on failure', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      const a1 = makeArtifact('artifact-001')
      useArtifactsStore.setState({ artifacts: [a1], totalArtifacts: 1 })
      server.use(
        http.delete('/api/v1/artifacts/:id', () =>
          HttpResponse.json(apiError('Delete failed'), { status: 500 }),
        ),
      )

      const result = await useArtifactsStore
        .getState()
        .deleteArtifact('artifact-001')

      expect(result).toBe(false)
      expect(useArtifactsStore.getState().artifacts).toEqual([a1])
      expect(useArtifactsStore.getState().totalArtifacts).toBe(1)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to delete artifact')
    })
  })

  describe('updateFromWsEvent', () => {
    it('triggers fetchArtifacts on WS event', async () => {
      let fetchCount = 0
      server.use(
        http.get('/api/v1/artifacts', () => {
          fetchCount += 1
          return HttpResponse.json(paginated([]))
        }),
      )

      const event: WsEvent = {
        event_type: 'artifact.created',
        channel: 'artifacts',
        timestamp: '2026-03-31T12:00:00Z',
        payload: { artifact_id: 'artifact-new', task_id: 'task-001' },
      }
      useArtifactsStore.getState().updateFromWsEvent(event)

      // The store schedules the refetch on the microtask queue. Use
      // ``vi.waitFor`` so the assertion polls the queue deterministically
      // rather than racing a fixed ``setTimeout(resolve, 0)`` against
      // the scheduler. ``waitFor`` flushes microtasks on each tick and
      // bails out as soon as the expectation passes.
      await vi.waitFor(() => {
        expect(fetchCount).toBeGreaterThan(0)
      })
    })
  })

  describe('filters', () => {
    it('sets search query with arbitrary strings', () => {
      fc.assert(
        fc.property(fc.string(), (s) => {
          useArtifactsStore.getState().setSearchQuery(s)
          return useArtifactsStore.getState().searchQuery === s
        }),
      )
    })

    it('sets type filter', () => {
      useArtifactsStore.getState().setTypeFilter('code')
      expect(useArtifactsStore.getState().typeFilter).toBe('code')
    })

    it('sets type filter to null for clear', () => {
      useArtifactsStore.getState().setTypeFilter('code')
      useArtifactsStore.getState().setTypeFilter(null)
      expect(useArtifactsStore.getState().typeFilter).toBeNull()
    })
  })

  describe('clearDetail', () => {
    it('clears detail state', () => {
      useArtifactsStore.setState({
        selectedArtifact: makeArtifact('artifact-001'),
        contentPreview: 'some content',
        detailError: 'old error',
      })

      useArtifactsStore.getState().clearDetail()

      const state = useArtifactsStore.getState()
      expect(state.selectedArtifact).toBeNull()
      expect(state.contentPreview).toBeNull()
      expect(state.detailError).toBeNull()
    })
  })
})
