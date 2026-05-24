import type { TaskStatus } from '@/api/types/enums'
import type { DashboardTask } from '@/api/types/tasks'
import { pendingTransitions } from './_state'
import type { TasksGet, TasksSet, TasksState } from './types'

export function createOptimisticActions(set: TasksSet, get: TasksGet) {
  return {
    optimisticTransition(taskId: string, targetStatus: TaskStatus): () => void {
      const prev = get().tasks
      const taskIdx = prev.findIndex((t) => t.id === taskId)
      if (taskIdx === -1) return () => {}
      pendingTransitions.add(taskId)
      const oldTask = prev[taskIdx]!
      const updated = { ...oldTask, status: targetStatus }
      const newTasks = [...prev]
      newTasks[taskIdx] = updated
      set({ tasks: newTasks })
      return () => {
        pendingTransitions.delete(taskId)
        set({ tasks: prev })
      }
    },

    upsertTask(task: DashboardTask): void {
      pendingTransitions.delete(task.id)
      set((s) => {
        const idx = s.tasks.findIndex((t) => t.id === task.id)
        const newTasks = idx === -1 ? [task, ...s.tasks] : [...s.tasks]
        if (idx !== -1) newTasks[idx] = task
        const selectedTask = s.selectedTask?.id === task.id
          ? task
          : s.selectedTask
        const patch: Partial<TasksState> = { tasks: newTasks, selectedTask }
        if (idx === -1) patch.total = s.total + 1
        return patch
      })
    },

    removeTask(taskId: string): void {
      set((s) => ({
        tasks: s.tasks.filter((t) => t.id !== taskId),
        total: Math.max(0, s.total - 1),
      }))
    },
  }
}
