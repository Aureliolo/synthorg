import { Circle, CircleCheck, CirclePlay, UserCheck, Clock } from 'lucide-react'
import type { DashboardTask } from '@/api/types/tasks'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'

interface TimelineEntry {
  readonly id: string
  readonly icon: typeof Circle
  readonly title: string
  readonly description?: string
  readonly timestamp?: string
  readonly tone: 'neutral' | 'accent' | 'success'
}

function buildEntries(task: DashboardTask): readonly TimelineEntry[] {
  const entries: TimelineEntry[] = []

  if (task.created_at) {
    entries.push({
      id: 'created',
      icon: CirclePlay,
      title: 'Created',
      description: task.created_by ? `by ${task.created_by}` : undefined,
      timestamp: task.created_at,
      tone: 'neutral',
    })
  }

  if (task.assigned_to) {
    entries.push({
      id: 'assigned',
      icon: UserCheck,
      title: 'Assigned',
      description: `to ${task.assigned_to}`,
      tone: 'accent',
    })
  }

  if (task.updated_at && task.updated_at !== task.created_at) {
    entries.push({
      id: 'updated',
      icon: Clock,
      title: 'Last updated',
      timestamp: task.updated_at,
      tone: 'neutral',
    })
  }

  if (task.status === 'completed') {
    entries.push({
      id: 'completed',
      icon: CircleCheck,
      title: 'Completed',
      timestamp: task.updated_at,
      tone: 'success',
    })
  }

  return entries
}

interface TaskDetailTimelineProps {
  task: DashboardTask
}

/**
 * Vertical timeline of task lifecycle milestones derived from the current
 * task state. The Task DTO does not yet expose a full event log, so this
 * component surfaces the durable fields (`created_at`, `assigned_to`,
 * `updated_at`, terminal status) rather than a backend-persisted history.
 */
export function TaskDetailTimeline({ task }: TaskDetailTimelineProps) {
  const entries = buildEntries(task)
  if (entries.length === 0) return null

  return (
    <div>
      {/*
        Rendered as a non-semantic label for consistency with
        TaskDetailMetadata (TaskDetailPage has no h1/h2 anchor, so using h3
        here would skip heading levels). The <ol> below owns the accessible
        name via aria-label.
      */}
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Timeline
      </span>
      <TaskStatusIndicator status={task.status} className="sr-only" />
      <ol className="mt-2 space-y-2" aria-label="Task lifecycle timeline">
        {entries.map((entry, idx) => {
          const Icon = entry.icon
          const isLast = idx === entries.length - 1
          return (
            <li key={entry.id} className="relative flex gap-3">
              {!isLast && (
                <span
                  aria-hidden="true"
                  className="absolute left-[7px] top-4 h-full w-px bg-border"
                />
              )}
              <Icon
                aria-hidden="true"
                className={cn('mt-0.5 size-4 shrink-0', {
                  'text-text-muted': entry.tone === 'neutral',
                  'text-accent': entry.tone === 'accent',
                  'text-success': entry.tone === 'success',
                })}
              />
              <div className="flex flex-col text-sm">
                <span className="text-foreground">
                  {entry.title}
                  {entry.description && (
                    <span className="ml-1 text-text-muted">{entry.description}</span>
                  )}
                </span>
                {entry.timestamp && (
                  <time
                    dateTime={entry.timestamp}
                    className="font-mono text-xs text-text-muted"
                  >
                    {formatDateTime(entry.timestamp)}
                  </time>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
