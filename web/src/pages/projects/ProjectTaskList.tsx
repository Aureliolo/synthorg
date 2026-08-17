import { Link } from 'react-router'
import { CheckCircle2 } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { EmptyState } from '@/components/ui/empty-state'
import { ROUTES } from '@/router/routes'
import { UNKNOWN_AGENT_NAME } from '@/utils/agents'
import type { Task } from '@/api/types/tasks'

interface ProjectTaskListProps {
  tasks: readonly Task[]
}

function ProjectTaskRow({ task }: { task: Task }) {
  return (
    <Link
      to={ROUTES.TASK_DETAIL.replace(':taskId', encodeURIComponent(task.id))}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent/5"
    >
      <TaskStatusIndicator status={task.status} />
      <span className="truncate text-sm text-foreground">{task.title}</span>
      {task.assigned_to && (
        <span className="ml-auto shrink-0 text-xs text-text-muted">
          {task.assigned_to_name ?? UNKNOWN_AGENT_NAME}
        </span>
      )}
    </Link>
  )
}

export function ProjectTaskList({ tasks }: ProjectTaskListProps) {
  if (tasks.length === 0) {
    return (
      <SectionCard title="Tasks" icon={CheckCircle2}>
        <EmptyState
          icon={CheckCircle2}
          title="No tasks"
          description="No tasks are linked to this project."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Tasks" icon={CheckCircle2}>
      <div className="flex flex-col gap-1">
        {tasks.map((task) => (
          <ProjectTaskRow key={task.id} task={task} />
        ))}
      </div>
    </SectionCard>
  )
}
