import { http, HttpResponse } from 'msw'
import type { Task } from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'
import { useTasksStore } from '@/stores/tasks'
import { apiError, apiSuccess, paginatedFor, voidSuccess } from '@/mocks/handlers'
import type { listTasks } from '@/api/endpoints/tasks'
import { server } from '@/test-setup'

// RIGHT-TO-LEFT OVERRIDE, built via fromCharCode so ESLint's
// ``security/detect-bidi-characters`` rule sees only hex in source.
// Sanitizer strips this character per the CVE-2021-42574 class.
const RLO = String.fromCharCode(0x202e)

const mockTask: Task = {
  id: 'task-1',
  title: 'Test task',
  description: 'A test task',
  type: 'development',
  status: 'assigned',
  priority: 'medium',
  project: 'test-project',
  created_by: 'agent-cto',
  assigned_to: 'agent-eng',
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  budget_limit: 10,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
  middleware_override: null,
  source: null,
  metadata: {},
  version: 1,
}

const mockTask2: Task = {
  ...mockTask,
  id: 'task-2',
  title: 'Second task',
  status: 'in_progress',
}

function paginated(
  data: Task[],
  meta: Partial<{ total: number; offset: number; limit: number }> = {},
) {
  const limit = meta.limit ?? 200
  return paginatedFor<typeof listTasks>({
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

function resetStore() {
  useTasksStore.setState({
    tasks: [],
    selectedTask: null,
    loading: false,
    loadingDetail: false,
    error: null,
  })
}

describe('useTasksStore', () => {
  beforeEach(() => {
    resetStore()
  })

  describe('fetchTasks', () => {
    it('sets loading to true during fetch', async () => {
      let release!: () => void
      const gate = new Promise<void>((resolve) => {
        release = resolve
      })
      server.use(
        http.get('/api/v1/tasks', async () => {
          await gate
          return HttpResponse.json(paginated([]))
        }),
      )
      const promise = useTasksStore.getState().fetchTasks()
      expect(useTasksStore.getState().loading).toBe(true)
      release()
      await promise
    })

    it('populates tasks on success', async () => {
      server.use(
        http.get('/api/v1/tasks', () =>
          HttpResponse.json(paginated([mockTask, mockTask2], { total: 2 })),
        ),
      )
      await useTasksStore.getState().fetchTasks()
      const state = useTasksStore.getState()
      expect(state.tasks).toHaveLength(2)
      expect(state.total).toBe(2)
      expect(state.loading).toBe(false)
      expect(state.error).toBeNull()
    })

    it('forwards filters as query params', async () => {
      const captured: { params: URLSearchParams | null } = { params: null }
      server.use(
        http.get('/api/v1/tasks', ({ request }) => {
          captured.params = new URL(request.url).searchParams
          return HttpResponse.json(paginated([]))
        }),
      )
      await useTasksStore
        .getState()
        .fetchTasks({ status: 'assigned', limit: 200 })
      expect(captured.params?.get('status')).toBe('assigned')
      expect(captured.params?.get('limit')).toBe('200')
    })

    it('sets error on failure', async () => {
      server.use(
        http.get('/api/v1/tasks', () =>
          HttpResponse.json(apiError('Network error')),
        ),
      )
      await useTasksStore.getState().fetchTasks()
      const state = useTasksStore.getState()
      expect(state.loading).toBe(false)
      expect(state.error).toBe('Network error')
    })
  })

  describe('fetchTask', () => {
    it('sets loadingDetail during fetch', async () => {
      let release!: () => void
      const gate = new Promise<void>((resolve) => {
        release = resolve
      })
      server.use(
        http.get('/api/v1/tasks/:id', async () => {
          await gate
          return HttpResponse.json(apiSuccess(mockTask))
        }),
      )
      const promise = useTasksStore.getState().fetchTask('task-1')
      expect(useTasksStore.getState().loadingDetail).toBe(true)
      release()
      await promise
    })

    it('sets selectedTask on success', async () => {
      server.use(
        http.get('/api/v1/tasks/:id', () =>
          HttpResponse.json(apiSuccess(mockTask)),
        ),
      )
      await useTasksStore.getState().fetchTask('task-1')
      expect(useTasksStore.getState().selectedTask).toEqual(mockTask)
      expect(useTasksStore.getState().loadingDetail).toBe(false)
    })

    it('sets error on failure', async () => {
      server.use(
        http.get('/api/v1/tasks/:id', () =>
          HttpResponse.json(apiError('Not found')),
        ),
      )
      await useTasksStore.getState().fetchTask('task-999')
      expect(useTasksStore.getState().loadingDetail).toBe(false)
      expect(useTasksStore.getState().error).toBe('Not found')
    })
  })

  describe('createTask', () => {
    it('prepends task to list and increments total', async () => {
      useTasksStore.setState({ tasks: [mockTask2], total: 1 })
      let capturedBody: unknown = null
      server.use(
        http.post('/api/v1/tasks', async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json(apiSuccess(mockTask))
        }),
      )
      const payload = {
        title: 'Test task',
        description: 'A test task',
        type: 'development' as const,
        project: 'test-project',
        created_by: 'agent-cto',
        budget_limit: 0,
      }
      const result = await useTasksStore.getState().createTask(payload)
      expect(result).toEqual(mockTask)
      expect(capturedBody).toEqual(payload)
      expect(useTasksStore.getState().tasks).toHaveLength(2)
      expect(useTasksStore.getState().tasks[0]!.id).toBe('task-1')
      expect(useTasksStore.getState().total).toBe(2)
    })

    it('returns null sentinel + emits error toast on failure', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      server.use(
        http.post('/api/v1/tasks', () =>
          HttpResponse.json(apiError('Validation failed'), { status: 400 }),
        ),
      )

      const result = await useTasksStore.getState().createTask({
        title: 'T',
        description: 'D',
        type: 'development',
        project: 'p',
        created_by: 'a',
        budget_limit: 0,
      })

      expect(result).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to create task')
      expect(toasts[0]!.description).toContain('Validation failed')
    })
  })

  describe('updateTask', () => {
    it('replaces task in list', async () => {
      const updated = { ...mockTask, title: 'Updated title' }
      useTasksStore.setState({ tasks: [mockTask, mockTask2], total: 2 })
      server.use(
        http.patch('/api/v1/tasks/:id', () =>
          HttpResponse.json(apiSuccess(updated)),
        ),
      )
      const result = await useTasksStore
        .getState()
        .updateTask('task-1', { title: 'Updated title', priority: null })
      expect(result?.title).toBe('Updated title')
      expect(useTasksStore.getState().tasks[0]!.title).toBe('Updated title')
    })
  })

  describe('transitionTask', () => {
    it('updates task status in list', async () => {
      const transitioned = { ...mockTask, status: 'in_progress' as const }
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      server.use(
        http.post('/api/v1/tasks/:id/transition', () =>
          HttpResponse.json(apiSuccess(transitioned)),
        ),
      )
      const result = await useTasksStore
        .getState()
        .transitionTask('task-1', { target_status: 'in_progress' })
      expect(result?.status).toBe('in_progress')
      expect(useTasksStore.getState().tasks[0]!.status).toBe('in_progress')
    })
  })

  describe('cancelTask', () => {
    it('updates task to cancelled', async () => {
      const cancelled = { ...mockTask, status: 'cancelled' as const }
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      server.use(
        http.post('/api/v1/tasks/:id/cancel', () =>
          HttpResponse.json(apiSuccess(cancelled)),
        ),
      )
      const result = await useTasksStore
        .getState()
        .cancelTask('task-1', { reason: 'No longer needed' })
      expect(result?.status).toBe('cancelled')
    })
  })

  describe('deleteTask', () => {
    it('removes task from list and decrements total', async () => {
      useTasksStore.setState({ tasks: [mockTask, mockTask2], total: 2 })
      server.use(
        http.delete('/api/v1/tasks/:id', () =>
          HttpResponse.json(voidSuccess()),
        ),
      )
      await useTasksStore.getState().deleteTask('task-1')
      expect(useTasksStore.getState().tasks).toHaveLength(1)
      expect(useTasksStore.getState().tasks[0]!.id).toBe('task-2')
      expect(useTasksStore.getState().total).toBe(1)
    })
  })

  describe('upsertTask', () => {
    it('inserts new task when not in list', () => {
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      useTasksStore.getState().upsertTask(mockTask2)
      expect(useTasksStore.getState().tasks).toHaveLength(2)
      expect(useTasksStore.getState().total).toBe(2)
    })

    it('replaces existing task by id', () => {
      const updated = { ...mockTask, title: 'New title' }
      useTasksStore.setState({ tasks: [mockTask, mockTask2], total: 2 })
      useTasksStore.getState().upsertTask(updated)
      expect(useTasksStore.getState().tasks).toHaveLength(2)
      expect(useTasksStore.getState().tasks[0]!.title).toBe('New title')
      expect(useTasksStore.getState().total).toBe(2)
    })
  })

  describe('removeTask', () => {
    it('removes task by id and decrements total', () => {
      useTasksStore.setState({ tasks: [mockTask, mockTask2], total: 2 })
      useTasksStore.getState().removeTask('task-1')
      expect(useTasksStore.getState().tasks).toHaveLength(1)
      expect(useTasksStore.getState().total).toBe(1)
    })

    it('does not go below zero total', () => {
      useTasksStore.setState({ tasks: [], total: 0 })
      useTasksStore.getState().removeTask('nonexistent')
      expect(useTasksStore.getState().total).toBe(0)
    })
  })

  describe('optimisticTransition', () => {
    it('updates task status and returns rollback function', () => {
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      const rollback = useTasksStore
        .getState()
        .optimisticTransition('task-1', 'in_progress')
      expect(useTasksStore.getState().tasks[0]!.status).toBe('in_progress')

      rollback()
      expect(useTasksStore.getState().tasks[0]!.status).toBe('assigned')
    })

    it('returns no-op for nonexistent task', () => {
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      const rollback = useTasksStore
        .getState()
        .optimisticTransition('nonexistent', 'in_progress')
      rollback()
      expect(useTasksStore.getState().tasks[0]!.status).toBe('assigned')
    })
  })

  describe('handleWsEvent', () => {
    it('upserts task from task.created event with full task payload', () => {
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: mockTask },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(1)
      expect(useTasksStore.getState().tasks[0]!.id).toBe('task-1')
    })

    it('upserts task from task.updated event', () => {
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      const updated = { ...mockTask, title: 'Updated via WS' }
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: updated },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks[0]!.title).toBe('Updated via WS')
    })

    it('upserts task from task.status_changed event', () => {
      useTasksStore.setState({ tasks: [mockTask], total: 1 })
      const changed = { ...mockTask, status: 'completed' as const }
      const event: WsEvent = {
        event_type: 'task.status_changed',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: changed },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks[0]!.status).toBe('completed')
    })

    it('ignores events without task payload', () => {
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { some_other_field: 'value' },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
    })

    it('ignores events with non-object truthy task value', () => {
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: 'task-id-string' },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
    })

    it('ignores events with task object missing required fields', () => {
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: { title: 'incomplete' } },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
    })

    it('rejects frame whose id carries bidi-override chars (sanitization mutates id)', () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const tainted = { ...mockTask, id: `task-1${RLO}` }
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: tainted },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
      errorSpy.mockRestore()
    })

    it('rejects frame where sanitization mutates assigned_to', () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const tainted = { ...mockTask, assigned_to: `agent-eng${RLO}` }
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: tainted },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
      errorSpy.mockRestore()
    })

    it('rejects frame where a reviewers entry carries bidi chars', () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const tainted = { ...mockTask, reviewers: ['agent-ok', `agent-bad${RLO}`] }
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: tainted },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
      errorSpy.mockRestore()
    })

    it('rejects frame where estimated_complexity is outside the enum', () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const tainted = {
        ...mockTask,
        estimated_complexity: 'galactic' as Task['estimated_complexity'],
      }
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: tainted },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
      errorSpy.mockRestore()
    })

    it('rejects frame where budget_limit is non-finite', () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const tainted = { ...mockTask, budget_limit: Number.NaN }
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: tainted },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(0)
      errorSpy.mockRestore()
    })

    it('accepts frame that omits assigned_to (undefined → null is normalisation, not mutation)', () => {
      const without: Record<string, unknown> = { ...mockTask }
      delete without.assigned_to
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: without },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(1)
      expect(useTasksStore.getState().tasks[0]!.assigned_to).toBeNull()
    })

    it('accepts frame that omits parent_task_id (undefined → null is normalisation, not mutation)', () => {
      const without: Record<string, unknown> = { ...mockTask }
      delete without.parent_task_id
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task: without },
      }
      useTasksStore.getState().handleWsEvent(event)
      expect(useTasksStore.getState().tasks).toHaveLength(1)
      expect(useTasksStore.getState().tasks[0]!.parent_task_id).toBeNull()
    })
  })
})
