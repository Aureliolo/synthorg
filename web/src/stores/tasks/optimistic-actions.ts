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
      // Targeted rollback: only revert the optimistic ``status`` field
      // on the current task object. Assigning the whole ``oldTask``
      // snapshot would clobber title / assignee / etc. that arrived
      // via a concurrent mutation or WS update during the request.
      return () => {
        pendingTransitions.delete(taskId)
        set((s) => {
          const currentIdx = s.tasks.findIndex((t) => t.id === taskId)
          if (currentIdx === -1) {
            // Task no longer exists (e.g. concurrent delete). Clear
            // selectedTask if it points at the gone task so the detail
            // drawer does not keep showing a phantom row.
            return {
              selectedTask: s.selectedTask?.id === taskId
                ? null
                : s.selectedTask,
            }
          }
          const currentTask = s.tasks[currentIdx]!
          const restored = [...s.tasks]
          restored[currentIdx] = { ...currentTask, status: oldTask.status }
          return {
            tasks: restored,
            selectedTask: s.selectedTask?.id === taskId
              ? restored[currentIdx]
              : s.selectedTask,
          }
        })
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
        // ``total`` is always derived from the resulting tasks array so
        // it can never drift from the actual count (a separate
        // increment can desync on rapid concurrent upserts).
        const patch: Partial<TasksState> = {
          tasks: newTasks,
          selectedTask,
          total: newTasks.length,
        }
        return patch
      })
    },

    removeTask(taskId: string): void {
      set((s) => {
        const tasks = s.tasks.filter((t) => t.id !== taskId)
        return { tasks, total: tasks.length }
      })
    },
  }
}
