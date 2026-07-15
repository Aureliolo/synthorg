import { waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useProjectsStore } from '@/stores/projects'
import { makeProject, makeTask } from '../helpers/factories'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import type { listProjects } from '@/api/endpoints/projects'
import type { listTasks } from '@/api/endpoints/tasks'
import { server } from '@/test-setup'
import type { Project } from '@/api/types/projects'
import type { Task } from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'

function paginatedProjects(data: Project[]) {
  return paginatedFor<typeof listProjects>({
    data,
    limit: 200,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit: 200,
      next_cursor: null,
      has_more: false,
    },
  })
}

function paginatedTasks(data: Task[]) {
  return paginatedFor<typeof listTasks>({
    data,
    limit: 50,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit: 50,
      next_cursor: null,
      has_more: false,
    },
  })
}

describe('useProjectsStore', () => {
  beforeEach(() => {
    useProjectsStore.setState({
      projects: [],
      nextCursor: null,
      hasMore: false,
      listLoading: false,
      listError: null,
      searchQuery: '',
      statusFilter: null,
      leadFilter: null,
      selectedProject: null,
      projectTasks: [],
      detailLoading: false,
      detailError: null,
      autonomyModeSaving: false,
    })
  })

  describe('fetchProjects', () => {
    it('populates projects on success', async () => {
      const project = makeProject('proj-001')
      server.use(
        http.get('/api/v1/projects', () =>
          HttpResponse.json(paginatedProjects([project])),
        ),
      )

      await useProjectsStore.getState().fetchProjects()

      const state = useProjectsStore.getState()
      expect(state.projects).toEqual([project])
      expect(state.projects.length).toBe(1)
      expect(state.listLoading).toBe(false)
    })

    it('sets error on failure', async () => {
      server.use(
        http.get('/api/v1/projects', () =>
          HttpResponse.json(apiError('Network error')),
        ),
      )

      await useProjectsStore.getState().fetchProjects()

      expect(useProjectsStore.getState().listError).toBe('Network error')
    })
  })

  describe('fetchProjectDetail', () => {
    it('populates selected project and tasks', async () => {
      const project = makeProject('proj-001')
      const task = makeTask('task-001')
      server.use(
        http.get('/api/v1/projects/:id', () =>
          HttpResponse.json(apiSuccess(project)),
        ),
        http.get('/api/v1/tasks', () =>
          HttpResponse.json(paginatedTasks([task])),
        ),
      )

      await useProjectsStore.getState().fetchProjectDetail('proj-001')

      const state = useProjectsStore.getState()
      expect(state.selectedProject).toEqual(project)
      expect(state.projectTasks).toEqual([task])
    })

    it('sets error when project not found', async () => {
      server.use(
        http.get('/api/v1/projects/:id', () =>
          HttpResponse.json(apiError('Not found')),
        ),
        http.get('/api/v1/tasks', () =>
          HttpResponse.json(apiError('Not found')),
        ),
      )

      await useProjectsStore.getState().fetchProjectDetail('missing')

      expect(useProjectsStore.getState().detailError).toBe('Not found')
    })

    it('handles partial task failure gracefully', async () => {
      const project = makeProject('proj-001')
      server.use(
        http.get('/api/v1/projects/:id', () =>
          HttpResponse.json(apiSuccess(project)),
        ),
        http.get('/api/v1/tasks', () =>
          HttpResponse.json(apiError('task fetch failed')),
        ),
      )

      await useProjectsStore.getState().fetchProjectDetail('proj-001')

      const state = useProjectsStore.getState()
      expect(state.selectedProject).toEqual(project)
      expect(state.projectTasks).toEqual([])
      expect(state.detailError).toMatch(/tasks/)
    })
  })

  describe('createProject', () => {
    it('calls API and optimistically adds to state', async () => {
      const project = makeProject('proj-new')
      let capturedBody: unknown = null
      server.use(
        http.post('/api/v1/projects', async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json(apiSuccess(project))
        }),
      )

      const result = await useProjectsStore
        .getState()
        .createProject({ name: 'New Project', description: '', team: [], budget: 0 })

      expect(result).toEqual(project)
      expect(capturedBody).toEqual({ name: 'New Project', description: '', team: [], budget: 0 })

      const state = useProjectsStore.getState()
      expect(state.projects).toContainEqual(project)
      expect(state.projects.length).toBe(1)
    })

    it('returns null sentinel + emits error toast on failure', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      server.use(
        http.post('/api/v1/projects', () =>
          HttpResponse.json(apiError('Create failed'), { status: 400 }),
        ),
      )

      const result = await useProjectsStore
        .getState()
        .createProject({ name: 'Fail', description: '', team: [], budget: 0 })

      expect(result).toBeNull()
      expect(useProjectsStore.getState().projects).toEqual([])
      expect(useProjectsStore.getState().projects.length).toBe(0)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to create project')
    })
  })

  describe('updateFromWsEvent', () => {
    it('triggers fetchProjects on WS event', async () => {
      let fetchCount = 0
      server.use(
        http.get('/api/v1/projects', () => {
          fetchCount += 1
          return HttpResponse.json(paginatedProjects([]))
        }),
      )

      const event: WsEvent = {
        event_type: 'project.created',
        channel: 'projects',
        version: 1,
        timestamp: '2026-03-31T12:00:00Z',
        payload: { project_id: 'proj-new', name: 'New' },
      }
      useProjectsStore.getState().updateFromWsEvent(event)

      await waitFor(() => {
        expect(fetchCount).toBeGreaterThan(0)
      })
    })
  })

  describe('filters', () => {
    it('sets search query', () => {
      useProjectsStore.getState().setSearchQuery('test')
      expect(useProjectsStore.getState().searchQuery).toBe('test')
    })

    it('sets status filter', () => {
      useProjectsStore.getState().setStatusFilter('active')
      expect(useProjectsStore.getState().statusFilter).toBe('active')
    })
  })

  describe('clearDetail', () => {
    it('clears detail state', () => {
      useProjectsStore.setState({
        selectedProject: makeProject('proj-001'),
        projectTasks: [makeTask('task-001')],
        detailError: 'old error',
      })

      useProjectsStore.getState().clearDetail()

      const state = useProjectsStore.getState()
      expect(state.selectedProject).toBeNull()
      expect(state.projectTasks).toEqual([])
      expect(state.detailError).toBeNull()
    })
  })

  describe('deleteProject', () => {
    it('removes the project optimistically and returns true on success', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001'), makeProject('proj-002')],
      })
      server.use(
        http.delete('/api/v1/projects/:id', () =>
          new HttpResponse(null, { status: 204 }),
        ),
      )

      const ok = await useProjectsStore.getState().deleteProject('proj-001')

      expect(ok).toBe(true)
      const state = useProjectsStore.getState()
      expect(state.projects.map((p) => p.id)).toEqual(['proj-002'])
      expect(state.projects.length).toBe(1)
    })

    it('rolls back the optimistic remove and returns false on API failure', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001'), makeProject('proj-002')],
      })
      server.use(
        http.delete('/api/v1/projects/:id', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      const ok = await useProjectsStore.getState().deleteProject('proj-001')

      expect(ok).toBe(false)
      const state = useProjectsStore.getState()
      expect(state.projects.map((p) => p.id)).toEqual(['proj-001', 'proj-002'])
      expect(state.projects.length).toBe(2)
    })
  })

  describe('batchDeleteProjects', () => {
    it('removes successfully deleted ids and reports the tally', async () => {
      useProjectsStore.setState({
        projects: [
          makeProject('proj-001'),
          makeProject('proj-002'),
          makeProject('proj-003'),
        ],
      })
      server.use(
        http.delete('/api/v1/projects/:id', () =>
          new HttpResponse(null, { status: 204 }),
        ),
      )

      const result = await useProjectsStore
        .getState()
        .batchDeleteProjects(['proj-001', 'proj-002'])

      expect(result).not.toBe(false)
      if (result === false) throw new Error('expected counts, not sentinel')
      expect(result.succeeded).toBe(2)
      expect(result.failed).toBe(0)
      expect(result.failedReasons).toEqual([])
      const state = useProjectsStore.getState()
      expect(state.projects.map((p) => p.id)).toEqual(['proj-003'])
      expect(state.projects.length).toBe(1)
    })

    it('keeps failed ids in the list and surfaces their reasons', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001'), makeProject('proj-002')],
      })
      server.use(
        http.delete('/api/v1/projects/:id', ({ params }) => {
          if (params['id'] === 'proj-001') {
            return new HttpResponse(null, { status: 204 })
          }
          return HttpResponse.json(apiError('boom'), { status: 500 })
        }),
      )

      const result = await useProjectsStore
        .getState()
        .batchDeleteProjects(['proj-001', 'proj-002'])

      // Partial success -> counts object (not the `false` sentinel which
      // is reserved for total-failure cases).
      expect(result).not.toBe(false)
      if (result === false) throw new Error('expected counts, not sentinel')
      expect(result.succeeded).toBe(1)
      expect(result.failed).toBe(1)
      // failedReasons holds only the human-readable reason so the
      // batch-toast helper can group identical reasons across the
      // failures; per-id context is logged separately via the
      // failedDetails channel inside the store.
      expect(result.failedReasons).toHaveLength(1)
      // 5xx now surfaces the backend's real (secret-redacted) error.
      expect(result.failedReasons[0]).toContain('boom')
      const state = useProjectsStore.getState()
      expect(state.projects.map((p) => p.id)).toEqual(['proj-002'])
    })
  })

  describe('setAutonomyMode', () => {
    function echoModeHandler(capture?: (body: unknown) => void) {
      return http.patch(
        '/api/v1/projects/:id/autonomy-mode',
        async ({ params, request }) => {
          const body = (await request.json()) as { mode: string | null }
          capture?.(body)
          return HttpResponse.json(
            apiSuccess(
              makeProject(String(params['id']), {
                autonomy_mode: body.mode as Project['autonomy_mode'],
              }),
            ),
          )
        },
      )
    }

    it('updates the list + selectedProject and clears the saving flag on success', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001'), makeProject('proj-002')],
        selectedProject: makeProject('proj-001'),
      })
      let capturedBody: unknown = null
      server.use(echoModeHandler((b) => (capturedBody = b)))

      const result = await useProjectsStore
        .getState()
        .setAutonomyMode('proj-001', 'locked')

      expect(result?.autonomy_mode).toBe('locked')
      // confirm defaults to false for a non-full transition; the displayed
      // project version rides along as the optimistic-concurrency guard.
      expect(capturedBody).toEqual({
        mode: 'locked',
        confirm: false,
        expected_version: 1,
      })
      const state = useProjectsStore.getState()
      expect(state.projects.find((p) => p.id === 'proj-001')?.autonomy_mode).toBe('locked')
      expect(state.projects.find((p) => p.id === 'proj-002')?.autonomy_mode).toBeNull()
      expect(state.selectedProject?.autonomy_mode).toBe('locked')
      expect(state.autonomyModeSaving).toBe(false)
    })

    it('forwards confirm=true for the deliberate full opt-in', async () => {
      useProjectsStore.setState({ projects: [makeProject('proj-001')] })
      let capturedBody: unknown = null
      server.use(echoModeHandler((b) => (capturedBody = b)))

      await useProjectsStore.getState().setAutonomyMode('proj-001', 'full', true)

      expect(capturedBody).toEqual({
        mode: 'full',
        confirm: true,
        expected_version: 1,
      })
    })

    it('omits expected_version when the project is absent from local state', async () => {
      useProjectsStore.setState({ projects: [], selectedProject: null })
      let capturedBody: unknown = null
      server.use(echoModeHandler((b) => (capturedBody = b)))

      await useProjectsStore.getState().setAutonomyMode('proj-001', 'locked')

      // No local row to source the version from: fall back to
      // last-write-wins rather than blocking the write.
      expect(capturedBody).toEqual({ mode: 'locked', confirm: false })
    })

    it('scopes the latest-wins guard per project so A is not invalidated by B', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-A'), makeProject('proj-B')],
      })
      server.use(echoModeHandler())

      // Two concurrent updates for DIFFERENT projects: a module-wide token
      // would treat the earlier project's response as stale once the later
      // one bumped the counter, dropping its returned state.
      const [resA, resB] = await Promise.all([
        useProjectsStore.getState().setAutonomyMode('proj-A', 'locked'),
        useProjectsStore.getState().setAutonomyMode('proj-B', 'semi'),
      ])

      expect(resA?.autonomy_mode).toBe('locked')
      expect(resB?.autonomy_mode).toBe('semi')
      const state = useProjectsStore.getState()
      expect(state.projects.find((p) => p.id === 'proj-A')?.autonomy_mode).toBe('locked')
      expect(state.projects.find((p) => p.id === 'proj-B')?.autonomy_mode).toBe('semi')
    })

    it('clears the mode back to inherit when passed null', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001', { autonomy_mode: 'full' })],
      })
      server.use(echoModeHandler())

      const result = await useProjectsStore
        .getState()
        .setAutonomyMode('proj-001', null)

      expect(result?.autonomy_mode).toBeNull()
      expect(useProjectsStore.getState().projects[0]?.autonomy_mode).toBeNull()
    })

    it('returns null sentinel and leaves state untouched on failure', async () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001', { autonomy_mode: 'semi' })],
        selectedProject: makeProject('proj-001', { autonomy_mode: 'semi' }),
      })
      server.use(
        http.patch('/api/v1/projects/:id/autonomy-mode', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      const result = await useProjectsStore
        .getState()
        .setAutonomyMode('proj-001', 'locked')

      expect(result).toBeNull()
      const state = useProjectsStore.getState()
      // Neither the list row nor the open detail is clobbered on failure.
      expect(state.projects[0]?.autonomy_mode).toBe('semi')
      expect(state.selectedProject?.autonomy_mode).toBe('semi')
      expect(state.autonomyModeSaving).toBe(false)
    })
  })

  describe('updateFromWsEvent autonomy_mode_changed', () => {
    it('applies the new mode to the list row and selectedProject', () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001'), makeProject('proj-002')],
        selectedProject: makeProject('proj-001'),
      })

      useProjectsStore.getState().updateFromWsEvent({
        event_type: 'project.autonomy_mode_changed',
        channel: 'projects',
        version: 1,
        timestamp: '2026-07-15T00:00:00Z',
        payload: { project_id: 'proj-001', new_mode: 'locked', new_version: 4 },
      } satisfies WsEvent)

      const state = useProjectsStore.getState()
      const updated = state.projects.find((p) => p.id === 'proj-001')
      expect(updated?.autonomy_mode).toBe('locked')
      // The server version rides along so a later guarded edit does not 409.
      expect(updated?.version).toBe(4)
      expect(state.projects.find((p) => p.id === 'proj-002')?.autonomy_mode).toBeNull()
      expect(state.selectedProject?.autonomy_mode).toBe('locked')
      expect(state.selectedProject?.version).toBe(4)
    })

    it('applies a raw null as a legitimate override clear', () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001', { autonomy_mode: 'locked' })],
        selectedProject: makeProject('proj-001', { autonomy_mode: 'locked' }),
      })

      useProjectsStore.getState().updateFromWsEvent({
        event_type: 'project.autonomy_mode_changed',
        channel: 'projects',
        version: 1,
        timestamp: '2026-07-15T00:00:00Z',
        payload: { project_id: 'proj-001', new_mode: null, new_version: 5 },
      } satisfies WsEvent)

      const state = useProjectsStore.getState()
      expect(state.projects[0]?.autonomy_mode).toBeNull()
      expect(state.projects[0]?.version).toBe(5)
      expect(state.selectedProject?.autonomy_mode).toBeNull()
      expect(state.selectedProject?.version).toBe(5)
    })

    it('drops a malformed non-null mode instead of clearing the override', () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001', { autonomy_mode: 'locked' })],
        selectedProject: makeProject('proj-001', { autonomy_mode: 'locked' }),
      })

      // A wire payload carrying an unknown mode is untrusted and must not
      // wrongly clear the displayed override; the periodic refetch reconciles.
      useProjectsStore.getState().updateFromWsEvent({
        event_type: 'project.autonomy_mode_changed',
        channel: 'projects',
        version: 1,
        timestamp: '2026-07-15T00:00:00Z',
        payload: { project_id: 'proj-001', new_mode: 'omniscient', new_version: 9 },
      } as unknown as WsEvent)

      const state = useProjectsStore.getState()
      // The whole event is dropped: neither mode nor version is applied.
      expect(state.projects[0]?.autonomy_mode).toBe('locked')
      expect(state.projects[0]?.version).toBe(1)
      expect(state.selectedProject?.autonomy_mode).toBe('locked')
    })
  })

  describe('updateFromWsEvent PROJECT_DELETED', () => {
    it('removes the project identified by payload.project_id before the refetch lands', () => {
      useProjectsStore.setState({
        projects: [makeProject('proj-001'), makeProject('proj-002')],
      })
      // Block the refetch so we can observe the pre-refetch state.
      server.use(
        http.get('/api/v1/projects', () =>
          HttpResponse.json(paginatedProjects([])),
        ),
      )

      useProjectsStore.getState().updateFromWsEvent({
        event_type: 'project.deleted',
        channel: 'projects',
        version: 1,
        timestamp: new Date().toISOString(),
        payload: { project_id: 'proj-001' },
      } satisfies WsEvent)

      // Local pruning is synchronous -- check before waiting for the refetch.
      const state = useProjectsStore.getState()
      expect(state.projects.map((p) => p.id)).toEqual(['proj-002'])
      expect(state.projects.length).toBe(1)
    })
  })
})
