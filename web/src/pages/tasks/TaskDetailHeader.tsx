import { InlineEdit } from '@/components/ui/inline-edit'
import { PriorityBadge, TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { useTasksStore } from '@/stores/tasks'
import type { Task } from '@/api/types/tasks'

interface TaskDetailHeaderProps {
  task: Task
}

export function TaskDetailHeader({ task }: TaskDetailHeaderProps) {
  return (
    <div className="flex items-start justify-between">
      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <TaskStatusIndicator status={task.status} label />
          <PriorityBadge priority={task.priority} />
        </div>
        <InlineEdit
          value={task.title}
          onSave={async (value) => {
            // Sentinel-return contract: the store logs + toasts on
            // failure and returns ``null``. Throwing when the result
            // is null is how InlineEdit is told to keep the input
            // open and surface its error state; the store already
            // owns the toast UX so we don't add another one here.
            // Title-only edit: omit ``priority`` so the persisted value is
            // preserved. Sending ``priority: null`` here would clear the
            // task's priority on every title rename.
            const updated = await useTasksStore.getState().updateTask(task.id, {
              title: value,
              expected_version: task.version,
            })
            if (!updated) {
              throw new Error('Failed to save title')
            }
          }}
          validate={(v) => (v.trim().length === 0 ? 'Title is required' : null)}
          className="text-xl font-semibold"
        />
      </div>
    </div>
  )
}
