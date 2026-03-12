import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTaskStore } from '@/stores/tasks'
import type { Task, WsEvent } from '@/api/types'

vi.mock('@/api/endpoints/tasks', () => ({
  listTasks: vi.fn(),
  getTask: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  transitionTask: vi.fn(),
  cancelTask: vi.fn(),
  deleteTask: vi.fn(),
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
})
