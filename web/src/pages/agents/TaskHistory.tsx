import { useMemo } from 'react'
import { ListTodo } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { TaskHistoryBar } from './TaskHistoryBar'
import type { Task } from '@/api/types/tasks'

interface TaskHistoryProps {
  tasks: readonly Task[]
  className?: string
}

export function TaskHistory({ tasks, className }: TaskHistoryProps) {
  // Sort by created_at descending (most recent first); skip tasks with invalid dates.
  // Memoised so a parent re-render with the same `tasks` reference doesn't
  // rebuild the array, re-parse every Date, and re-sort.
  const sorted = useMemo(() => {
    return [...tasks]
      .filter((t) => t.created_at && !Number.isNaN(new Date(t.created_at).getTime()))
      .sort((a, b) => new Date(b.created_at!).getTime() - new Date(a.created_at!).getTime())
  }, [tasks])

  // Compute max duration for relative bar widths.
  // Mirrors effectiveEndMs logic: fall back to created_at if updated_at is
  // unparseable or earlier.
  const maxDurationMs = useMemo(() => {
    return sorted.reduce((max, task) => {
      const createdMs = new Date(task.created_at!).getTime()
      const endRaw = task.updated_at ?? task.created_at!
      const endMs = new Date(endRaw).getTime()
      const end = (Number.isNaN(endMs) || endMs < createdMs) ? createdMs : endMs
      const duration = end - createdMs
      return Math.max(max, duration)
    }, 1)
  }, [sorted])

  return (
    <SectionCard title="Task History" icon={ListTodo} className={className}>
      {sorted.length === 0 ? (
        <EmptyState
          icon={ListTodo}
          title="No tasks yet"
          description="Task history will appear here as tasks are assigned."
        />
      ) : (
        <StaggerGroup className="space-y-1">
          {sorted.slice(0, 20).map((task) => (
            <StaggerItem key={task.id}>
              <TaskHistoryBar task={task} maxDurationMs={maxDurationMs} />
            </StaggerItem>
          ))}
        </StaggerGroup>
      )}
    </SectionCard>
  )
}
