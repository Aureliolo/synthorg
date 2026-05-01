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
      expect(state.detailError).toMatch(/content preview/)
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

      // The store debounces/schedules the refetch; allow microtasks to flush.
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(fetchCount).toBeGreaterThan(0)
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
