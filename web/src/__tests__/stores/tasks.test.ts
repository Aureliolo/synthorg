import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTaskStore } from '@/stores/tasks'
import type { Task, WsEvent } from '@/api/types'

const mockListTasks = vi.fn()
const mockCreateTask = vi.fn()
const mockUpdateTask = vi.fn()
const mockTransitionTask = vi.fn()
const mockCancelTask = vi.fn()

vi.mock('@/api/endpoints/tasks', () => ({
  listTasks: (...args: unknown[]) => mockListTasks(...args),
  createTask: (...args: unknown[]) => mockCreateTask(...args),
  updateTask: (...args: unknown[]) => mockUpdateTask(...args),
  transitionTask: (...args: unknown[]) => mockTransitionTask(...args),
  cancelTask: (...args: unknown[]) => mockCancelTask(...args),
}))

const mockTask: Task = {
  id: 'task-1',
  title: 'Test Task',
  description: 'A test task',
  type: 'development',
  status: 'created',
  priority: 'medium',
  project: 'test-project',
  created_by: 'agent-1',
  assigned_to: null,
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  budget_limit: 10.0,
  cost_usd: 0.0,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
  version: 1,
  created_at: '2026-03-12T10:00:00Z',
  updated_at: '2026-03-12T10:00:00Z',
}

describe('useTaskStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initializes with empty state', () => {
    const store = useTaskStore()
    expect(store.tasks).toEqual([])
    expect(store.total).toBe(0)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('computes tasksByStatus correctly', () => {
    const store = useTaskStore()
    store.tasks = [mockTask, { ...mockTask, id: 'task-2', status: 'in_progress' }]
    expect(store.tasksByStatus['created']).toHaveLength(1)
    expect(store.tasksByStatus['in_progress']).toHaveLength(1)
  })

  describe('fetchTasks', () => {
    it('fetches tasks and sets state', async () => {
      mockListTasks.mockResolvedValue({ data: [mockTask], total: 1 })

      const store = useTaskStore()
      await store.fetchTasks({ limit: 10 })

      expect(store.tasks).toEqual([mockTask])
      expect(store.total).toBe(1)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('sets error on failure', async () => {
      mockListTasks.mockRejectedValue(new Error('Network error'))

      const store = useTaskStore()
      await store.fetchTasks()

      expect(store.tasks).toEqual([])
      expect(store.error).toBe('Network error')
      expect(store.loading).toBe(false)
    })
  })

  describe('createTask', () => {
    it('appends task to list on success', async () => {
      const newTask = { ...mockTask, id: 'task-new' }
      mockCreateTask.mockResolvedValue(newTask)

      const store = useTaskStore()
      store.total = 0
      const result = await store.createTask({
        title: 'New Task',
        description: 'desc',
        type: 'development',
        project: 'test',
        created_by: 'agent-1',
      })

      expect(result).toEqual(newTask)
      expect(store.tasks).toHaveLength(1)
      expect(store.total).toBe(1)
    })

    it('returns null and sets error on failure', async () => {
      mockCreateTask.mockRejectedValue(new Error('Conflict'))

      const store = useTaskStore()
      const result = await store.createTask({
        title: 'New',
        description: 'desc',
        type: 'development',
        project: 'test',
        created_by: 'agent-1',
      })

      expect(result).toBeNull()
      expect(store.error).toBe('Conflict')
    })
  })

  describe('updateTask', () => {
    it('replaces task in list on success', async () => {
      const updated = { ...mockTask, title: 'Updated Title' }
      mockUpdateTask.mockResolvedValue(updated)

      const store = useTaskStore()
      store.tasks = [mockTask]
      const result = await store.updateTask('task-1', { title: 'Updated Title' })

      expect(result).toEqual(updated)
      expect(store.tasks[0].title).toBe('Updated Title')
    })

    it('returns null on failure', async () => {
      mockUpdateTask.mockRejectedValue(new Error('Not found'))

      const store = useTaskStore()
      const result = await store.updateTask('task-1', { title: 'x' })

      expect(result).toBeNull()
      expect(store.error).toBe('Not found')
    })
  })

  describe('transitionTask', () => {
    it('replaces task in list on success', async () => {
      const transitioned = { ...mockTask, status: 'assigned' as const }
      mockTransitionTask.mockResolvedValue(transitioned)

      const store = useTaskStore()
      store.tasks = [mockTask]
      const result = await store.transitionTask('task-1', {
        target_status: 'assigned',
        expected_version: 1,
      })

      expect(result).toEqual(transitioned)
      expect(store.tasks[0].status).toBe('assigned')
    })

    it('returns null and sets error on failure', async () => {
      mockTransitionTask.mockRejectedValue(new Error('Version conflict'))

      const store = useTaskStore()
      store.tasks = [mockTask]
      const result = await store.transitionTask('task-1', {
        target_status: 'assigned',
        expected_version: 1,
      })

      expect(result).toBeNull()
      expect(store.error).toBe('Version conflict')
      expect(store.tasks[0].status).toBe('created')
    })
  })

  describe('cancelTask', () => {
    it('replaces task in list on success', async () => {
      const cancelled = { ...mockTask, status: 'cancelled' as const }
      mockCancelTask.mockResolvedValue(cancelled)

      const store = useTaskStore()
      store.tasks = [mockTask]
      const result = await store.cancelTask('task-1', { reason: 'done' })

      expect(result).toEqual(cancelled)
      expect(store.tasks[0].status).toBe('cancelled')
    })

    it('returns null and sets error on failure', async () => {
      mockCancelTask.mockRejectedValue(new Error('Forbidden'))

      const store = useTaskStore()
      store.tasks = [mockTask]
      const result = await store.cancelTask('task-1', { reason: 'done' })

      expect(result).toBeNull()
      expect(store.error).toBe('Forbidden')
      expect(store.tasks[0].status).toBe('created')
    })
  })

  describe('WS events', () => {
    it('handles task.created WS event', () => {
      const store = useTaskStore()
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { ...mockTask },
      }
      store.handleWsEvent(event)
      expect(store.tasks).toHaveLength(1)
      expect(store.total).toBe(1)
    })

    it('handles task.updated WS event', () => {
      const store = useTaskStore()
      store.tasks = [mockTask]
      const event: WsEvent = {
        event_type: 'task.updated',
        channel: 'tasks',
        timestamp: '2026-03-12T10:01:00Z',
        payload: { id: 'task-1', title: 'Updated Title' },
      }
      store.handleWsEvent(event)
      expect(store.tasks[0].title).toBe('Updated Title')
    })

    it('does not duplicate tasks on repeated task.created events', () => {
      const store = useTaskStore()
      store.tasks = [mockTask]
      store.total = 1
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { ...mockTask },
      }
      store.handleWsEvent(event)
      expect(store.tasks).toHaveLength(1)
    })

    it('skips task.created WS events when filters are active', () => {
      const store = useTaskStore()
      store.currentFilters = { status: 'in_progress' }
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { ...mockTask, id: 'task-new', status: 'created' },
      }
      store.handleWsEvent(event)
      // Should NOT append — filters are active, let next fetch sync the list
      expect(store.tasks).toHaveLength(0)
      expect(store.total).toBe(0)
    })
  })
})
