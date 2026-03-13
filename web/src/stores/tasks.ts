import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as tasksApi from '@/api/endpoints/tasks'
import { getErrorMessage } from '@/utils/errors'
import type {
  Task,
  TaskStatus,
  TaskFilters,
  CreateTaskRequest,
  UpdateTaskRequest,
  TransitionTaskRequest,
  CancelTaskRequest,
  WsEvent,
} from '@/api/types'

export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref<Task[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentFilters = ref<TaskFilters>({})

  const tasksByStatus = computed<Partial<Record<TaskStatus, Task[]>>>(() => {
    const grouped: Partial<Record<TaskStatus, Task[]>> = {}
    for (const task of tasks.value) {
      const existing = grouped[task.status]
      if (existing) {
        existing.push(task)
      } else {
        grouped[task.status] = [task]
      }
    }
    return grouped
  })

  /** Check whether any non-trivial filters are currently active. */
  function hasActiveFilters(): boolean {
    return Object.values(currentFilters.value).some(
      (v) => v !== undefined && v !== null && !(typeof v === 'string' && v.trim() === ''),
    )
  }

  async function fetchTasks(filters?: TaskFilters) {
    loading.value = true
    error.value = null
    // Always update filters — passing undefined clears previous filters
    currentFilters.value = filters ? { ...filters } : {}
    try {
      const result = await tasksApi.listTasks(currentFilters.value)
      tasks.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function createTask(data: CreateTaskRequest): Promise<Task | null> {
    error.value = null
    try {
      const task = await tasksApi.createTask(data)
      // Only append to local list if no filters are active (filtered views are kept
      // accurate by REST fetches) and guard against race with WS task.created event
      if (!hasActiveFilters() && !tasks.value.some((t) => t.id === task.id)) {
        tasks.value = [...tasks.value, task]
        total.value++
      }
      return task
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  async function updateTask(taskId: string, data: UpdateTaskRequest): Promise<Task | null> {
    error.value = null
    try {
      const updated = await tasksApi.updateTask(taskId, data)
      tasks.value = tasks.value.map((t) => (t.id === taskId ? updated : t))
      return updated
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  async function transitionTask(
    taskId: string,
    data: TransitionTaskRequest,
  ): Promise<Task | null> {
    error.value = null
    try {
      const updated = await tasksApi.transitionTask(taskId, data)
      tasks.value = tasks.value.map((t) => (t.id === taskId ? updated : t))
      return updated
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  async function cancelTask(taskId: string, data: CancelTaskRequest): Promise<Task | null> {
    error.value = null
    try {
      const updated = await tasksApi.cancelTask(taskId, data)
      tasks.value = tasks.value.map((t) => (t.id === taskId ? updated : t))
      return updated
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  /** Runtime check for required Task fields before insertion. */
  function isValidTaskPayload(p: Record<string, unknown>): boolean {
    return (
      typeof p.id === 'string' && p.id !== '' &&
      typeof p.title === 'string' &&
      typeof p.status === 'string' &&
      typeof p.type === 'string' &&
      typeof p.priority === 'string' &&
      typeof p.created_by === 'string'
    )
  }

  function handleWsEvent(event: WsEvent) {
    const payload = event.payload as Record<string, unknown> | null
    if (!payload || typeof payload !== 'object') return
    switch (event.event_type) {
      case 'task.created':
        if (
          isValidTaskPayload(payload) &&
          !tasks.value.some((t) => t.id === payload.id)
        ) {
          // Only append if no active filters — filtered views are kept accurate by REST fetches
          if (!hasActiveFilters()) {
            tasks.value = [...tasks.value, payload as unknown as Task]
            total.value++
          }
        }
        break
      case 'task.updated':
      case 'task.status_changed':
      case 'task.assigned':
        if (typeof payload.id === 'string' && payload.id) {
          // Only update tasks already in the list — if filters are active,
          // tasks that no longer match will be cleaned up on next REST fetch
          tasks.value = tasks.value.map((t) =>
            t.id === payload.id ? { ...t, ...(payload as Partial<Task>) } : t,
          )
        }
        break
    }
  }

  return {
    tasks,
    total,
    loading,
    error,
    currentFilters,
    tasksByStatus,
    fetchTasks,
    createTask,
    updateTask,
    transitionTask,
    cancelTask,
    handleWsEvent,
  }
})
